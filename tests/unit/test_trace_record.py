"""Trace records and the per-request trace (specs/004-trace-legs research R3a, R8)."""

import json
import logging
import re
from datetime import UTC, datetime

import pytest
from httpx_sse import ServerSentEvent

from llm_pii_proxy.tracing.record import Leg, RequestTrace, TraceRecord

SECRET_TEXT = "ann@example.com"


def make_record(**overrides) -> TraceRecord:
    fields = {
        "time": datetime(2026, 11, 24, 10, 15, 2, 123456, tzinfo=UTC),
        "session_id": "sess-1",
        "request_id": "abc",
        "leg": Leg.UPSTREAM_OUT,
        "route": "/v1/messages",
        "status": 200,
        "complete": True,
        "payload": {"text": "héllo"},
    }
    return TraceRecord(**fields | overrides)


def test_line_has_contract_keys_in_order():
    line = make_record().to_line()
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    parsed = json.loads(line)
    assert list(parsed) == [
        "time",
        "session_id",
        "request_id",
        "leg",
        "route",
        "status",
        "complete",
        "payload",
    ]
    assert parsed["time"] == "2026-11-24T10:15:02.123Z"
    assert parsed["leg"] == "upstream_out"


def test_line_keeps_non_ascii():
    assert "héllo".encode() in make_record().to_line()


def test_record_is_immutable():
    with pytest.raises(AttributeError):
        make_record().status = 500  # type: ignore[misc]


class RecordingTap:
    def __init__(self, fail_on: Leg | None = None) -> None:
        self.records: list[TraceRecord] = []
        self.fail_on = fail_on

    def record(self, record: TraceRecord) -> None:
        if record.leg is self.fail_on:
            raise OSError("disk full")
        self.records.append(record)


def make_trace(tap, legs=frozenset(Leg)) -> RequestTrace:
    return RequestTrace(tap, legs, session_id="sess-1", route="/v1/messages")


def ev(name: str, data: dict | str) -> ServerSentEvent:
    return ServerSentEvent(event=name, data=data if isinstance(data, str) else json.dumps(data))


def test_wants_only_enabled_legs():
    trace = make_trace(RecordingTap(), frozenset({Leg.UPSTREAM_IN}))
    assert trace.wants(Leg.UPSTREAM_IN)
    assert not trace.wants(Leg.UPSTREAM_OUT)


def test_disabled_leg_is_not_written():
    tap = RecordingTap()
    trace = make_trace(tap, frozenset({Leg.UPSTREAM_IN}))
    trace.capture(Leg.UPSTREAM_OUT, b'{"a": 1}')
    trace.capture(Leg.UPSTREAM_IN, b'{"b": 2}')
    trace.finish(200)
    assert [r.leg for r in tap.records] == [Leg.UPSTREAM_IN]


def test_finish_writes_in_leg_order_once():
    tap = RecordingTap()
    trace = make_trace(tap)
    trace.capture(Leg.UPSTREAM_IN, b'{"b": 2}')
    trace.capture(Leg.UPSTREAM_OUT, b'{"a": 1}')
    trace.finish(200)
    trace.finish(200)
    assert [r.leg for r in tap.records] == [Leg.UPSTREAM_OUT, Leg.UPSTREAM_IN]
    assert [r.payload for r in tap.records] == [{"a": 1}, {"b": 2}]
    assert all(r.complete for r in tap.records)


def test_records_share_request_fields():
    tap = RecordingTap()
    trace = make_trace(tap)
    trace.capture(Leg.UPSTREAM_OUT, b"{}")
    trace.capture(Leg.UPSTREAM_IN, b"{}")
    trace.finish(200)
    out, back = tap.records
    assert re.fullmatch(r"[0-9a-f]{32}", out.request_id)
    assert out.request_id == back.request_id
    assert {r.session_id for r in tap.records} == {"sess-1"}
    assert {r.route for r in tap.records} == {"/v1/messages"}
    assert make_trace(tap).request_id != out.request_id


def test_upstream_legs_carry_upstream_status():
    tap = RecordingTap()
    trace = make_trace(tap)
    trace.capture(Leg.UPSTREAM_OUT, b"{}")
    trace.upstream_responded(200)
    trace.capture(Leg.UPSTREAM_IN, b"{}")
    trace.finish(500)
    assert [r.status for r in tap.records] == [200, 200]


def test_without_upstream_response_legs_carry_finish_status():
    tap = RecordingTap()
    trace = make_trace(tap)
    trace.capture(Leg.UPSTREAM_OUT, b"{}")
    trace.capture(Leg.UPSTREAM_IN, b"{}")
    trace.finish(502)
    assert [r.status for r in tap.records] == [502, 502]


def test_non_json_body_recorded_as_text():
    tap = RecordingTap()
    trace = make_trace(tap)
    trace.capture(Leg.UPSTREAM_IN, b"overloaded \xff")
    trace.finish(529)
    assert tap.records[0].payload == "overloaded �"


def test_fed_leg_is_assembled():
    tap = RecordingTap()
    trace = make_trace(tap)
    trace.feed(Leg.UPSTREAM_IN, ev("message_start", {"message": {"id": "m", "content": []}}))
    trace.finish(200)
    record = tap.records[0]
    assert record.payload == {"id": "m", "content": []}
    assert record.complete is False


def test_failing_tap_drops_only_that_leg(caplog):
    tap = RecordingTap(fail_on=Leg.UPSTREAM_OUT)
    trace = make_trace(tap)
    trace.capture(Leg.UPSTREAM_OUT, SECRET_TEXT.encode())
    trace.capture(Leg.UPSTREAM_IN, b"{}")
    with caplog.at_level(logging.WARNING):
        trace.finish(200)
    assert [r.leg for r in tap.records] == [Leg.UPSTREAM_IN]
    assert "upstream_out" in caplog.text and "OSError" in caplog.text
    assert SECRET_TEXT not in caplog.text and "disk full" not in caplog.text


def test_assembler_failure_drops_only_that_leg(caplog):
    tap = RecordingTap()
    trace = make_trace(tap)
    trace.capture(Leg.UPSTREAM_OUT, b"{}")
    with caplog.at_level(logging.WARNING):
        trace.feed(Leg.UPSTREAM_IN, ev("message_start", SECRET_TEXT))
        trace.feed(Leg.UPSTREAM_IN, ev("message_stop", {}))
        trace.finish(200)
    assert [r.leg for r in tap.records] == [Leg.UPSTREAM_OUT]
    assert "upstream_in" in caplog.text and "JSONDecodeError" in caplog.text
    assert SECRET_TEXT not in caplog.text
