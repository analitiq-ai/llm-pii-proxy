"""Streamed response restoration (contracts/http-interface.md Responses, data-model.md)."""

import json

from httpx_sse import ServerSentEvent

from llm_pii_proxy.report import ScrubReport
from llm_pii_proxy.sse import encode, restore_stream
from llm_pii_proxy.vault import Category, Vault


def ev(name: str, data: dict | str) -> ServerSentEvent:
    return ServerSentEvent(event=name, data=data if isinstance(data, str) else json.dumps(data))


def delta(index: int, kind: str, value: str) -> ServerSentEvent:
    field = "text" if kind == "text_delta" else "partial_json"
    return ev(
        "content_block_delta",
        {"type": "content_block_delta", "index": index, "delta": {"type": kind, field: value}},
    )


def stop(index: int) -> ServerSentEvent:
    return ev("content_block_stop", {"type": "content_block_stop", "index": index})


async def run(events, vault):
    async def source():
        for e in events:
            yield e

    report = ScrubReport()
    restored = [e async for e in restore_stream(source(), vault, report)]
    return restored, report.unissued


def parse(events: list[ServerSentEvent]) -> list[tuple[str, str]]:
    return [(e.event, e.data) for e in events]


def make_vault():
    v = Vault()
    v.assign("ann@x.com", Category.EMAIL)
    v.assign('q"b\\s', Category.PERSON)
    return v


def payload(event):
    return json.loads(event[1])


async def test_other_events_unchanged_and_in_order():
    events = [
        ev("message_start", {"type": "message_start", "message": {"id": "m", "content": []}}),
        ev("ping", {"type": "ping"}),
        ev(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "PII_EMAIL_0001"},
            },
        ),
        ev(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "signature_delta", "signature": "s"},
            },
        ),
        ev("future_event", "not json PII_EMAIL_0001"),
        ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}),
        ev("message_stop", {"type": "message_stop"}),
        ev("error", {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}),
    ]
    restored, _ = await run(events, make_vault())
    assert parse(restored) == [(e.event, e.data) for e in events]


async def test_text_delta_restored_raw_with_holdback():
    events = [delta(0, "text_delta", "to PII_EM"), delta(0, "text_delta", "AIL_0001!"), stop(0)]
    out = parse((await run(events, make_vault()))[0])
    assert [payload(e)["delta"]["text"] for e in out[:2]] == ["to ", "ann@x.com!"]
    assert out[2][0] == "content_block_stop"


async def test_input_json_delta_restored_json_escaped():
    events = [delta(1, "input_json_delta", '{"a": "PII_PERSON_0001"}'), stop(1)]
    out = parse((await run(events, make_vault()))[0])
    assert json.loads(payload(out[0])["delta"]["partial_json"]) == {"a": 'q"b\\s'}


async def test_held_remainder_flushed_as_extra_delta_before_stop():
    events = [delta(0, "text_delta", "end PII_EMAIL_00"), stop(0)]
    out = parse((await run(events, make_vault()))[0])
    assert [e[0] for e in out] == [
        "content_block_delta",
        "content_block_delta",
        "content_block_stop",
    ]
    assert payload(out[1]) == {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "PII_EMAIL_00"},
    }


async def test_one_restorer_per_block_index():
    events = [
        delta(0, "text_delta", "a PII_EMA"),
        delta(1, "input_json_delta", '{"k": "PII_EMA'),
        delta(0, "text_delta", "IL_0001"),
        delta(1, "input_json_delta", 'IL_0001"}'),
        stop(0),
        stop(1),
    ]
    out = parse((await run(events, make_vault()))[0])
    texts = [payload(e)["delta"] for e in out if e[0] == "content_block_delta"]
    assert texts[0]["text"] + texts[2]["text"] == "a ann@x.com"
    assert json.loads(texts[1]["partial_json"] + texts[3]["partial_json"]) == {"k": "ann@x.com"}


async def test_unissued_counted():
    events = [delta(0, "text_delta", "PII_EMAIL_0009 "), stop(0)]
    _, unissued = await run(events, make_vault())
    assert unissued == 1


async def test_non_ascii_preserved():
    v = Vault()
    v.assign("Zoë", Category.PERSON)
    events = [delta(0, "text_delta", "hi PII_PERSON_0001 ✓"), stop(0)]
    out = parse((await run(events, v))[0])
    assert payload(out[0])["delta"]["text"] == "hi Zoë ✓"


def test_encode_writes_one_event_with_every_field():
    event = ServerSentEvent(event="message_delta", data="line one\nline two", id="7", retry=1000)
    assert encode(event) == (
        b"event: message_delta\nid: 7\nretry: 1000\ndata: line one\ndata: line two\n\n"
    )


def test_encode_writes_no_id_for_an_event_without_one():
    # httpx_sse gives an event without an id line an empty id, not None.
    assert encode(ServerSentEvent(event="ping", data="{}")) == b"event: ping\ndata: {}\n\n"


async def test_closing_the_stream_closes_its_source():
    closed = False

    async def source():
        nonlocal closed
        try:
            yield delta(0, "text_delta", "a")
            yield delta(0, "text_delta", "b")
        finally:
            closed = True

    restored = restore_stream(source(), make_vault(), ScrubReport())
    await anext(restored)
    await restored.aclose()
    assert closed
