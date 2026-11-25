"""Trace records, the output interface, and the per-request trace handlers record through."""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from httpx_sse import ServerSentEvent

from llm_pii_proxy.tracing.assemble import MessageAssembler

log = logging.getLogger(__name__)


class Leg(StrEnum):
    """In the order a request crosses them, which is the order its records are written in."""

    CLIENT_IN = "client_in"
    UPSTREAM_OUT = "upstream_out"
    UPSTREAM_IN = "upstream_in"
    CLIENT_OUT = "client_out"

    @property
    def raw(self) -> bool:
        """Holds real values: the client side of the proxy, before scrubbing and after restoring."""
        return self in (Leg.CLIENT_IN, Leg.CLIENT_OUT)


@dataclass(frozen=True, slots=True)
class TraceRecord:
    time: datetime
    session_id: str | None
    request_id: str
    leg: Leg
    route: str
    status: int
    complete: bool
    payload: Any

    def to_line(self) -> bytes:
        line = {
            "time": _utc_millis(self.time),
            "session_id": self.session_id,
            "request_id": self.request_id,
            "leg": self.leg.value,
            "route": self.route,
            "status": self.status,
            "complete": self.complete,
            "payload": self.payload,
        }
        return (json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


class Tap(Protocol):
    """An output for trace records. May raise: only RequestTrace calls it, inside its guard."""

    def record(self, record: TraceRecord) -> None: ...


class NullTap:
    def record(self, record: TraceRecord) -> None:
        pass


class RequestTrace:
    """What crossed the selected legs of one request, written to the tap by `finish`.

    Feeding a stream event and writing a record, the steps that act on payload content or the tap,
    are guarded: a failure drops that leg and logs only the leg and exception type. Tracing must
    never change or fail the request it observes.
    """

    def __init__(
        self, tap: Tap, legs: frozenset[Leg], *, session_id: str | None, route: str
    ) -> None:
        self._tap = tap
        self._legs = set(legs)
        self.session_id = session_id
        self.route = route
        self.request_id = uuid.uuid4().hex
        self._bodies: dict[Leg, bytes] = {}
        self._streams: dict[Leg, MessageAssembler] = {}
        self._upstream_status: int | None = None
        self._finished = False

    def wants(self, leg: Leg) -> bool:
        return leg in self._legs

    def capture(self, leg: Leg, body: bytes) -> None:
        if leg in self._legs:
            self._bodies[leg] = body

    def open_stream(self, leg: Leg) -> None:
        """Called when the leg's stream starts, so it is recorded even if no event ever crosses."""
        if leg in self._legs:
            self._streams[leg] = MessageAssembler()

    def feed(self, leg: Leg, event: ServerSentEvent) -> None:
        if leg not in self._legs:
            return
        try:
            self._streams[leg].feed(event)
        except Exception as e:  # noqa: BLE001 - any trace failure must stay out of the request
            self._drop(leg, e)

    def upstream_responded(self, status: int) -> None:
        self._upstream_status = status

    def finish(self, status: int) -> None:
        if self._finished:
            return
        self._finished = True
        now = datetime.now(UTC)
        for leg in Leg:
            if leg not in self._legs or (leg not in self._bodies and leg not in self._streams):
                continue
            try:
                self._tap.record(self._build(leg, now, status))
            except Exception as e:  # noqa: BLE001 - any trace failure must stay out of the request
                self._drop(leg, e)

    def _build(self, leg: Leg, now: datetime, status: int) -> TraceRecord:
        if leg in self._streams:
            assembler = self._streams[leg]
            payload, complete = assembler.message(), assembler.complete
        else:
            payload, complete = _decode(self._bodies[leg]), True
        # Client legs carry the status the client got; upstream legs Anthropic's, or the client's
        # when Anthropic never answered.
        answered = self._upstream_status
        leg_status = status if leg.raw or answered is None else answered
        return TraceRecord(
            time=now,
            session_id=self.session_id,
            request_id=self.request_id,
            leg=leg,
            route=self.route,
            status=leg_status,
            complete=complete,
            payload=payload,
        )

    def _drop(self, leg: Leg, error: Exception) -> None:
        self._legs.discard(leg)
        log.warning("trace leg=%s dropped: %s", leg.value, type(error).__name__)


def _utc_millis(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _decode(body: bytes) -> Any:
    try:
        return json.loads(body)
    except ValueError:
        return body.decode(errors="replace")
