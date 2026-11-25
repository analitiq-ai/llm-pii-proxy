"""Streamed responses (US1 scenario 5, contracts/http-interface.md Responses)."""

import json
import logging

import httpx
import pytest

from llm_pii_proxy.tracing.record import Leg
from llm_pii_proxy.vault import Category
from tests.integration.conftest import SESSION, post_and_disconnect


def sse(*events: tuple[str, dict]) -> bytes:
    return b"".join(f"event: {e}\ndata: {json.dumps(d)}\n\n".encode() for e, d in events)


FIELDS = {
    "text_delta": "text",
    "input_json_delta": "partial_json",
    "thinking_delta": "thinking",
    "signature_delta": "signature",
}


def delta(index, kind, value):
    field = FIELDS[kind]
    return (
        "content_block_delta",
        {"type": "content_block_delta", "index": index, "delta": {"type": kind, field: value}},
    )


def parse_sse(raw: bytes) -> list[tuple[str, str]]:
    out = []
    for block in raw.decode().split("\n\n"):
        if block:
            fields = dict(line.split(": ", 1) for line in block.split("\n"))
            out.append((fields["event"], fields["data"]))
    return out


async def stream_through(proxy, upstream, user_text, events):
    upstream.post("/v1/messages").mock(
        return_value=httpx.Response(
            200, content=sse(*events), headers={"content-type": "text/event-stream"}
        )
    )
    body = {"model": "m", "stream": True, "messages": [{"role": "user", "content": user_text}]}
    r = await proxy.post("/v1/messages", json=body, headers=SESSION)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    return parse_sse(r.content)


async def test_tokens_split_across_chunks_restored(make_proxy, regex_detector, upstream):
    events = [
        ("message_start", {"type": "message_start", "message": {"id": "m", "content": []}}),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        delta(0, "text_delta", "Reply to PII_E"),
        delta(0, "text_delta", "MAIL_00"),
        delta(0, "text_delta", "01 today"),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_stop", {"type": "message_stop"}),
    ]
    proxy = make_proxy([regex_detector])
    out = await stream_through(proxy, upstream, "from ann@corp.example", events)
    assert [e for e, _ in out] == [e for e, _ in events]
    text = "".join(json.loads(d)["delta"]["text"] for e, d in out if e == "content_block_delta")
    assert text == "Reply to ann@corp.example today"
    assert all("PII_" not in d for _, d in out)


async def test_streamed_tool_input_with_quote_stays_valid_json(
    make_proxy, regex_detector, upstream
):
    events = [
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "tool_use", "id": "t", "name": "Write", "input": {}},
            },
        ),
        delta(1, "input_json_delta", '{"content": "to: PII_PER'),
        delta(1, "input_json_delta", 'SON_0001"}'),
        ("content_block_stop", {"type": "content_block_stop", "index": 1}),
    ]
    value = 'O"Neil \\ Ltd'
    proxy = make_proxy([regex_detector], always_scrub={Category.PERSON: (value,)})
    out = await stream_through(proxy, upstream, f"send to {value}", events)
    partial = "".join(
        json.loads(d)["delta"]["partial_json"] for e, d in out if e == "content_block_delta"
    )
    assert json.loads(partial) == {"content": f"to: {value}"}


async def test_thinking_and_signature_deltas_byte_identical(make_proxy, regex_detector, upstream):
    thinking = delta(0, "thinking_delta", "consider PII_EMAIL_0001")
    signature = delta(0, "signature_delta", "c2ln")
    events = [
        thinking,
        signature,
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ]
    proxy = make_proxy([regex_detector])
    out = await stream_through(proxy, upstream, "a@b.com", events)
    assert out[:2] == [(e, json.dumps(d)) for e, d in events[:2]]


class UpstreamBody(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self.chunks = chunks
        self.read = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.read += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.parametrize("after_chunks", [0, 1], ids=["before body", "mid body"])
async def test_client_disconnect_closes_upstream_stream(
    make_proxy, regex_detector, upstream, after_chunks
):
    event = sse(("message_start", {"type": "message_start", "message": {"content": []}}))
    upstream_body = UpstreamBody(*[event] * 100)
    upstream.post("/v1/messages").mock(
        return_value=httpx.Response(
            200, stream=upstream_body, headers={"content-type": "text/event-stream"}
        )
    )
    body = {"model": "m", "stream": True, "messages": [{"role": "user", "content": "hi"}]}
    await post_and_disconnect(
        make_proxy([regex_detector]), "/v1/messages", body, after_chunks=after_chunks
    )
    assert upstream_body.read < 100, "the proxy read the whole stream: the client never cut it off"
    assert upstream_body.closed


@pytest.mark.parametrize("legs", [frozenset(), frozenset(Leg)], ids=["untraced", "traced"])
async def test_client_disconnect_logs_unissued_tokens_of_open_block(
    make_proxy, regex_detector, upstream, caplog, legs
):
    events = [
        ("message_start", {"type": "message_start", "message": {"content": []}}),
        (
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        ),
        # PII_EMAIL_0001 is issued for the request's address; only PII_EMAIL_0009 is unissued.
        delta(0, "text_delta", "PII_EMAIL_0001 PII_EMAIL_0009 "),
        delta(0, "text_delta", "more"),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ]
    upstream.post("/v1/messages").mock(
        return_value=httpx.Response(
            200,
            stream=UpstreamBody(*(sse(event) for event in events)),
            headers={"content-type": "text/event-stream"},
        )
    )
    body = {"model": "m", "stream": True, "messages": [{"role": "user", "content": "ann@x.com"}]}
    with caplog.at_level(logging.INFO):
        await post_and_disconnect(
            make_proxy([regex_detector], trace_legs=legs), "/v1/messages", body, after_chunks=3
        )
    (line,) = [r.getMessage() for r in caplog.records if "unissued=" in r.getMessage()]
    assert "unissued=1" in line
