"""Payload traces per leg."""

import json
import logging
import os

import httpx
import pytest

from llm_pii_proxy.app import SESSION_HEADER
from llm_pii_proxy.tracing.file_tap import FileTap
from llm_pii_proxy.tracing.record import Leg, TraceRecord
from tests.integration.conftest import SESSION, message_response, post_and_disconnect, sent

EMAIL = "ann@example.com"
PLACEHOLDER = "PII_EMAIL_0001"
BOTH = frozenset({Leg.UPSTREAM_OUT, Leg.UPSTREAM_IN})
ALL = frozenset(Leg)
BODY = {"model": "m", "messages": [{"role": "user", "content": f"mail {EMAIL}"}]}
STREAM_BODY = BODY | {"stream": True}


class FailingTap:
    def record(self, record: TraceRecord) -> None:
        raise OSError("disk full")


@pytest.fixture
def trace_dir(tmp_path):
    return tmp_path / "traces"


@pytest.fixture
def traced(make_proxy, regex_detector, trace_dir):
    def make(legs=BOTH, tap=None):
        if tap is None:
            tap = FileTap.open(trace_dir, 3600, os.getuid())
        return make_proxy([regex_detector], tap=tap, trace_legs=legs)

    return make


def records(trace_dir) -> list[dict]:
    lines = [
        line for path in sorted(trace_dir.glob("*.jsonl")) for line in path.read_text().splitlines()
    ]
    return [json.loads(line) for line in lines]


def all_trace_bytes(trace_dir) -> bytes:
    return b"".join(p.read_bytes() for p in trace_dir.glob("*"))


def sse(*events: tuple[str, dict | str]) -> bytes:
    return b"".join(
        f"event: {name}\ndata: {data if isinstance(data, str) else json.dumps(data)}\n\n".encode()
        for name, data in events
    )


def stream_response(raw: bytes) -> httpx.Response:
    return httpx.Response(200, content=raw, headers={"content-type": "text/event-stream"})


MESSAGE_START = (
    "message_start",
    {"type": "message_start", "message": {"id": "m1", "role": "assistant", "content": []}},
)
TEXT_BLOCK = (
    "content_block_start",
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
)
TEXT_DELTA = (
    "content_block_delta",
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": f"Hi {PLACEHOLDER}"},
    },
)
BLOCK_STOP = ("content_block_stop", {"type": "content_block_stop", "index": 0})
MESSAGE_DELTA = (
    "message_delta",
    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}},
)
MESSAGE_STOP = ("message_stop", {"type": "message_stop"})
FULL_STREAM = (MESSAGE_START, TEXT_BLOCK, TEXT_DELTA, BLOCK_STOP, MESSAGE_DELTA, MESSAGE_STOP)


async def test_tracing_off_writes_nothing(traced, upstream, trace_dir):
    upstream.post("/v1/messages").mock(return_value=message_response("ok"))
    r = await traced(legs=frozenset()).post("/v1/messages", json=BODY, headers=SESSION)
    assert r.status_code == 200
    assert list(trace_dir.iterdir()) == []


async def test_non_streamed_request_records_both_scrubbed_legs(traced, upstream, trace_dir):
    upstream.post("/v1/messages").mock(return_value=message_response(f"Hi {PLACEHOLDER}"))
    r = await traced().post("/v1/messages", json=BODY, headers=SESSION)
    assert r.json()["content"][0]["text"] == f"Hi {EMAIL}"
    out, back = records(trace_dir)
    assert [out["leg"], back["leg"]] == ["upstream_out", "upstream_in"]
    assert out["request_id"] == back["request_id"]
    assert {out["session_id"], back["session_id"]} == {SESSION["x-claude-code-session-id"]}
    assert {out["route"], back["route"]} == {"/v1/messages"}
    assert (out["status"], back["status"]) == (200, 200)
    assert out["complete"] and back["complete"]
    assert out["payload"] == sent(upstream)
    assert back["payload"]["content"][0]["text"] == f"Hi {PLACEHOLDER}"
    assert EMAIL.encode() not in all_trace_bytes(trace_dir)


async def test_streamed_response_recorded_as_assembled_message(traced, upstream, trace_dir):
    upstream.post("/v1/messages").mock(return_value=stream_response(sse(*FULL_STREAM)))
    r = await traced().post("/v1/messages", json=STREAM_BODY, headers=SESSION)
    assert EMAIL in r.text
    _, back = records(trace_dir)
    assert back["leg"] == "upstream_in"
    assert back["complete"] is True
    assert back["payload"] == {
        "id": "m1",
        "role": "assistant",
        "content": [{"type": "text", "text": f"Hi {PLACEHOLDER}"}],
        "stop_reason": "end_turn",
        "usage": {"output_tokens": 3},
    }
    assert EMAIL.encode() not in all_trace_bytes(trace_dir)


async def test_only_selected_leg_recorded(traced, upstream, trace_dir):
    upstream.post("/v1/messages").mock(return_value=message_response("ok"))
    await traced(legs=frozenset({Leg.UPSTREAM_OUT})).post(
        "/v1/messages", json=BODY, headers=SESSION
    )
    assert [r["leg"] for r in records(trace_dir)] == ["upstream_out"]


async def test_count_tokens_traced_and_models_not(traced, upstream, trace_dir):
    upstream.post("/v1/messages/count_tokens").mock(
        return_value=httpx.Response(200, json={"input_tokens": 5})
    )
    upstream.get("/v1/models").mock(return_value=httpx.Response(200, json={"data": []}))
    proxy = traced()
    await proxy.post("/v1/messages/count_tokens", json=BODY, headers=SESSION)
    await proxy.get("/v1/models", headers=SESSION)
    traces = records(trace_dir)
    assert [(r["leg"], r["route"]) for r in traces] == [
        ("upstream_out", "/v1/messages/count_tokens"),
        ("upstream_in", "/v1/messages/count_tokens"),
    ]
    assert traces[1]["payload"] == {"input_tokens": 5}


async def test_upstream_error_recorded_on_both_legs(traced, upstream, trace_dir):
    error = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
    upstream.post("/v1/messages").mock(return_value=httpx.Response(529, json=error))
    r = await traced().post("/v1/messages", json=BODY, headers=SESSION)
    assert r.status_code == 529
    out, back = records(trace_dir)
    assert (out["leg"], back["leg"]) == ("upstream_out", "upstream_in")
    assert (out["status"], back["status"]) == (529, 529)
    assert back["payload"] == error


async def test_unreachable_upstream_records_request_with_proxy_status(traced, upstream, trace_dir):
    upstream.post("/v1/messages").mock(side_effect=httpx.ConnectError("refused"))
    r = await traced().post("/v1/messages", json=BODY, headers=SESSION)
    assert r.status_code == 502
    (out,) = records(trace_dir)
    assert (out["leg"], out["status"]) == ("upstream_out", 502)
    assert PLACEHOLDER in json.dumps(out["payload"])


class FailingDetector:
    async def detect(self, texts):
        raise RuntimeError("detector crashed")


@pytest.mark.parametrize("rejection", ["invalid body", "scrubbing failed"])
async def test_rejected_request_forwards_and_records_nothing(
    make_proxy, regex_detector, upstream, trace_dir, rejection
):
    tap = FileTap.open(trace_dir, 3600, os.getuid())
    if rejection == "invalid body":
        proxy, request = make_proxy([regex_detector], tap=tap, trace_legs=BOTH), {"content": b"{"}
    else:
        proxy, request = make_proxy([FailingDetector()], tap=tap, trace_legs=BOTH), {"json": BODY}
    r = await proxy.post("/v1/messages", headers=SESSION, **request)
    assert r.status_code == 400
    assert not upstream.calls
    assert records(trace_dir) == []


@pytest.mark.parametrize("tap", ["working", "failing"])
async def test_traced_requests_log_no_content(traced, upstream, trace_dir, caplog, tap):
    sent_text, answer_text = "sent-text-marker", "answer-text-marker"
    body = {"model": "m", "messages": [{"role": "user", "content": f"{sent_text} {EMAIL}"}]}
    upstream.post("/v1/messages").mock(
        side_effect=[
            message_response(f"{answer_text} {PLACEHOLDER}"),
            stream_response(sse(*FULL_STREAM)),
            httpx.Response(400, json={"type": "error", "error": {"message": answer_text}}),
        ]
    )
    proxy = traced(legs=ALL, tap=FailingTap() if tap == "failing" else None)
    with caplog.at_level(logging.DEBUG):
        for request in (body, body | {"stream": True}, body):
            await proxy.post("/v1/messages", json=request, headers=SESSION)
    assert ("dropped" in caplog.text) == (tap == "failing")
    for content in (sent_text, answer_text, EMAIL, PLACEHOLDER):
        assert content not in caplog.text


# Completeness and isolation from the request


async def test_stream_without_message_stop_is_incomplete(traced, upstream, trace_dir):
    upstream.post("/v1/messages").mock(return_value=stream_response(sse(*FULL_STREAM[:-1])))
    await traced().post("/v1/messages", json=STREAM_BODY, headers=SESSION)
    assert records(trace_dir)[1]["complete"] is False


async def test_stream_with_no_events_recorded_empty_and_incomplete(traced, upstream, trace_dir):
    upstream.post("/v1/messages").mock(return_value=stream_response(b""))
    await traced().post("/v1/messages", json=STREAM_BODY, headers=SESSION)
    back = records(trace_dir)[1:]
    assert [(t["leg"], t["payload"], t["complete"]) for t in back] == [("upstream_in", {}, False)]


async def test_stream_with_error_event_is_incomplete(traced, upstream, trace_dir):
    error = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
    # message_stop after the error: only the error can make the record incomplete.
    upstream.post("/v1/messages").mock(
        return_value=stream_response(sse(*FULL_STREAM[:-1], ("error", error), MESSAGE_STOP))
    )
    await traced().post("/v1/messages", json=STREAM_BODY, headers=SESSION)
    back = records(trace_dir)[1]
    assert back["complete"] is False
    assert back["payload"]["error"] == error


async def test_event_the_trace_cannot_parse_reaches_client_as_without_tracing(
    traced, upstream, trace_dir
):
    raw = sse(("message_start", "not json"), TEXT_BLOCK, MESSAGE_STOP)
    upstream.post("/v1/messages").mock(side_effect=lambda request: stream_response(raw))
    untraced = await traced(legs=frozenset()).post(
        "/v1/messages", json=STREAM_BODY, headers=SESSION
    )
    r = await traced().post("/v1/messages", json=STREAM_BODY, headers=SESSION)
    assert r.status_code == untraced.status_code == 200
    assert r.content == untraced.content
    assert b"not json" in r.content
    assert [t["leg"] for t in records(trace_dir)] == ["upstream_out"]


@pytest.mark.parametrize("streamed", [False, True])
async def test_tracing_never_changes_traffic(
    make_proxy, regex_detector, upstream, trace_dir, streamed
):
    def answer():
        if streamed:
            return stream_response(sse(*FULL_STREAM))
        return message_response(f"Hi {PLACEHOLDER}")

    upstream.post("/v1/messages").mock(side_effect=lambda request: answer())
    body = STREAM_BODY if streamed else BODY
    setups = {
        "off": {},
        "on": {"tap": FileTap.open(trace_dir, 3600, os.getuid()), "trace_legs": ALL},
        "failing": {"tap": FailingTap(), "trace_legs": ALL},
    }
    seen = {}
    for name, setup in setups.items():
        r = await make_proxy([regex_detector], **setup).post(
            "/v1/messages", json=body, headers=SESSION
        )
        sent_request = upstream.calls[-1].request
        seen[name] = (
            r.status_code,
            dict(r.headers),
            r.content,
            sent_request.method,
            str(sent_request.url),
            dict(sent_request.headers),
            sent_request.content,
        )
    assert seen["on"] == seen["off"] == seen["failing"]
    assert len(records(trace_dir)) == 4


@pytest.mark.parametrize(
    ("after_chunks", "crossed"),
    [
        (
            0,
            [
                ("client_in", True),
                ("upstream_out", True),
                ("upstream_in", False),
                ("client_out", False),
            ],
        ),
        (
            1,
            [
                ("client_in", True),
                ("upstream_out", True),
                ("upstream_in", False),
                ("client_out", False),
            ],
        ),
    ],
    ids=["before body", "mid body"],
)
async def test_stream_cut_off_by_client_records_what_crossed(
    traced, upstream, trace_dir, after_chunks, crossed
):
    upstream.post("/v1/messages").mock(return_value=stream_response(sse(*FULL_STREAM)))
    await post_and_disconnect(
        traced(legs=ALL), "/v1/messages", STREAM_BODY, after_chunks=after_chunks
    )
    traces = records(trace_dir)
    assert [(t["leg"], t["complete"]) for t in traces] == crossed
    assert all(t["status"] == 200 for t in traces)


async def test_all_legs_recorded_in_order_under_one_request_id(traced, upstream, trace_dir):
    upstream.post("/v1/messages").mock(return_value=message_response(f"Hi {PLACEHOLDER}"))
    r = await traced(legs=ALL).post("/v1/messages", json=BODY, headers=SESSION)
    traces = records(trace_dir)
    assert [t["leg"] for t in traces] == ["client_in", "upstream_out", "upstream_in", "client_out"]
    assert len({t["request_id"] for t in traces}) == 1
    assert {t["status"] for t in traces} == {200}
    client_in, out, back, client_out = (t["payload"] for t in traces)
    assert client_in == BODY
    assert out == sent(upstream)
    assert EMAIL not in json.dumps(out) and PLACEHOLDER in json.dumps(out)
    assert back["content"][0]["text"] == f"Hi {PLACEHOLDER}"
    assert client_out == r.json()
    assert client_out["content"][0]["text"] == f"Hi {EMAIL}"


async def test_streamed_client_out_is_the_assembled_restored_message(traced, upstream, trace_dir):
    upstream.post("/v1/messages").mock(return_value=stream_response(sse(*FULL_STREAM)))
    await traced(legs=ALL).post("/v1/messages", json=STREAM_BODY, headers=SESSION)
    *_, back, client_out = records(trace_dir)
    assert client_out["leg"] == "client_out"
    assert client_out["complete"] is True
    restored = [{"type": "text", "text": f"Hi {EMAIL}"}]
    assert client_out["payload"] == back["payload"] | {"content": restored}


@pytest.mark.parametrize(
    ("headers", "content", "detector", "session_id", "file"),
    [
        (SESSION, b"{", "regex", "sess-1", "sess-1.jsonl"),
        (SESSION, json.dumps(BODY).encode(), "failing", "sess-1", "sess-1.jsonl"),
        ({}, json.dumps(BODY).encode(), "regex", None, "_no-session.jsonl"),
        ({SESSION_HEADER: ""}, json.dumps(BODY).encode(), "regex", None, "_no-session.jsonl"),
    ],
    ids=["invalid JSON", "scrubbing failed", "no session header", "empty session header"],
)
async def test_request_rejected_before_forwarding_records_only_client_in(
    make_proxy, regex_detector, upstream, trace_dir, headers, content, detector, session_id, file
):
    detectors = [regex_detector] if detector == "regex" else [FailingDetector()]
    proxy = make_proxy(detectors, tap=FileTap.open(trace_dir, 3600, os.getuid()), trace_legs=ALL)
    r = await proxy.post("/v1/messages", content=content, headers=headers)
    assert r.status_code == 400
    assert not upstream.calls
    assert [p.name for p in trace_dir.iterdir()] == [file]
    (client_in,) = records(trace_dir)
    assert (client_in["leg"], client_in["status"]) == ("client_in", 400)
    assert client_in["session_id"] == session_id
    assert client_in["payload"] == ("{" if content == b"{" else BODY)


async def test_unreachable_upstream_records_client_in_and_upstream_out_with_502(
    traced, upstream, trace_dir
):
    upstream.post("/v1/messages").mock(side_effect=httpx.ConnectError("refused"))
    r = await traced(legs=ALL).post("/v1/messages", json=BODY, headers=SESSION)
    assert r.status_code == 502
    assert [(t["leg"], t["status"]) for t in records(trace_dir)] == [
        ("client_in", 502),
        ("upstream_out", 502),
    ]


async def test_forwarded_upstream_error_recorded_on_client_out(traced, upstream, trace_dir):
    error = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
    upstream.post("/v1/messages").mock(return_value=httpx.Response(529, json=error))
    r = await traced(legs=ALL).post("/v1/messages", json=BODY, headers=SESSION)
    traces = records(trace_dir)
    assert [(t["leg"], t["status"]) for t in traces] == [
        ("client_in", 529),
        ("upstream_out", 529),
        ("upstream_in", 529),
        ("client_out", 529),
    ]
    assert traces[-1]["payload"] == r.json() == error


async def test_unexpected_exception_records_client_in_with_500(traced, upstream, trace_dir):
    upstream.post("/v1/messages").mock(return_value=httpx.Response(200, content=b"not json"))
    with pytest.raises(json.JSONDecodeError):
        await traced(legs=ALL).post("/v1/messages", json=BODY, headers=SESSION)
    assert [(t["leg"], t["status"]) for t in records(trace_dir)] == [
        ("client_in", 500),
        ("upstream_out", 200),
        ("upstream_in", 200),
    ]
