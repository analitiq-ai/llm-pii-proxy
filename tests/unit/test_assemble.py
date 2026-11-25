"""Assembling a streamed response into one message."""

import json

from httpx_sse import ServerSentEvent

from llm_pii_proxy.tracing.assemble import MessageAssembler

FIELDS = {
    "text_delta": "text",
    "input_json_delta": "partial_json",
    "thinking_delta": "thinking",
    "signature_delta": "signature",
}


def ev(name: str, data: dict) -> ServerSentEvent:
    return ServerSentEvent(event=name, data=json.dumps({"type": name} | data))


def start_message() -> ServerSentEvent:
    return ev(
        "message_start",
        {
            "message": {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 1},
            }
        },
    )


def start_block(index: int, block: dict) -> ServerSentEvent:
    return ev("content_block_start", {"index": index, "content_block": block})


def delta(index: int, kind: str, value: str) -> ServerSentEvent:
    return ev("content_block_delta", {"index": index, "delta": {"type": kind, FIELDS[kind]: value}})


def stop_block(index: int) -> ServerSentEvent:
    return ev("content_block_stop", {"index": index})


def end_message() -> list[ServerSentEvent]:
    return [
        ev(
            "message_delta",
            {
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"output_tokens": 42},
            },
        ),
        ev("message_stop", {}),
    ]


def assemble(events) -> MessageAssembler:
    assembler = MessageAssembler()
    for event in events:
        assembler.feed(event)
    return assembler


FULL_STREAM = [
    start_message(),
    ev("ping", {}),
    start_block(0, {"type": "thinking", "thinking": "", "signature": ""}),
    delta(0, "thinking_delta", "Let me "),
    delta(0, "thinking_delta", "think."),
    delta(0, "signature_delta", "sig=="),
    stop_block(0),
    start_block(1, {"type": "text", "text": ""}),
    delta(1, "text_delta", "Hello "),
    delta(1, "text_delta", "PII_EMAIL_0001"),
    stop_block(1),
    start_block(2, {"type": "tool_use", "id": "t1", "name": "Read", "input": {}}),
    delta(2, "input_json_delta", '{"path": '),
    delta(2, "input_json_delta", '"/a"}'),
    stop_block(2),
    *end_message(),
]


def test_full_stream_matches_non_streamed_shape():
    assembler = assemble(FULL_STREAM)
    assert assembler.complete
    assert assembler.message() == {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "Let me think.", "signature": "sig=="},
            {"type": "text", "text": "Hello PII_EMAIL_0001"},
            {"type": "tool_use", "id": "t1", "name": "Read", "input": {"path": "/a"}},
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 42},
    }


def test_incomplete_without_message_stop():
    assembler = assemble(FULL_STREAM[:-1])
    assert not assembler.complete
    assert assembler.message()["stop_reason"] == "tool_use"


def test_unstopped_tool_block_keeps_partial_json():
    assembler = assemble([*FULL_STREAM[:13]])
    assert not assembler.complete
    assert assembler.message()["content"][2]["input"] == '{"path": '


def test_unparsable_tool_json_kept_as_string():
    events = [
        start_message(),
        start_block(0, {"type": "tool_use", "id": "t1", "name": "Read", "input": {}}),
        delta(0, "input_json_delta", '{"path": '),
        stop_block(0),
        *end_message(),
    ]
    assert assemble(events).message()["content"][0]["input"] == '{"path": '


def test_tool_without_arguments_has_empty_input():
    events = [
        start_message(),
        start_block(0, {"type": "tool_use", "id": "t1", "name": "Now", "input": {}}),
        delta(0, "input_json_delta", ""),
        stop_block(0),
        *end_message(),
    ]
    assert assemble(events).message()["content"][0]["input"] == {}


def test_error_event_kept_and_incomplete():
    error = {"type": "overloaded_error", "message": "Overloaded"}
    assembler = assemble([*FULL_STREAM[:5], ev("error", {"error": error}), ev("message_stop", {})])
    assert not assembler.complete
    assert assembler.message()["error"] == {"type": "error", "error": error}


def test_unknown_events_and_deltas_ignored():
    events = [
        start_message(),
        ev("future_event", {"x": 1}),
        start_block(0, {"type": "text", "text": ""}),
        ev("content_block_delta", {"index": 0, "delta": {"type": "future_delta", "v": 1}}),
        stop_block(0),
        *end_message(),
    ]
    known = [events[0], *events[2:3], *events[4:]]
    with_unknown, without = assemble(events), assemble(known)
    assert with_unknown.message() == without.message()
    assert with_unknown.complete and without.complete


def cite(index: int, citation: dict) -> ServerSentEvent:
    return ev(
        "content_block_delta",
        {"index": index, "delta": {"type": "citations_delta", "citation": citation}},
    )


def test_citations_appended_to_their_block():
    first, second = ({"type": "char_location", "cited_text": t} for t in ("x", "y"))
    events = [
        start_message(),
        start_block(0, {"type": "text", "text": ""}),
        start_block(1, {"type": "text", "text": ""}),
        cite(0, first),
        cite(0, second),
        delta(0, "text_delta", "cited"),
        stop_block(0),
        stop_block(1),
        *end_message(),
    ]
    assert assemble(events).message()["content"] == [
        {"type": "text", "text": "cited", "citations": [first, second]},
        {"type": "text", "text": ""},
    ]


def test_nothing_fed_gives_empty_incomplete_message():
    assembler = MessageAssembler()
    assert assembler.message() == {}
    assert not assembler.complete
