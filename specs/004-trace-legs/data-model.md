# Data Model: Payload traces per leg

## Leg

Enum of the legs in [spec.md § Legs](spec.md#legs), declared in leg order. US1 defines `upstream_out` and
`upstream_in`; US2 adds `client_in` (first) and `client_out` (last) and the property `raw` (true for those two).

## TraceRecord

The one record type (FR-004). Serialized as in [contracts/trace-record.md](contracts/trace-record.md).

| Field | Type | Rule |
|---|---|---|
| `time` | datetime (UTC) | when the record is built |
| `session_id` | str \| None | None only for a request rejected for having none (US2) |
| `request_id` | str | R10; same for every leg of one request |
| `leg` | Leg | |
| `route` | str | request path, e.g. `/v1/messages` |
| `status` | int | R8: Anthropic's status on upstream legs, or the proxy's when no response came; the client's status on client legs |
| `complete` | bool | false only for a streamed leg that did not reach `message_stop` or carried an `error` event (R3, FR-007) |
| `payload` | JSON value | the parsed JSON body; a body that is not JSON is recorded as its text, invalid UTF-8 replaced; a streamed leg's assembled message |

Immutable (frozen dataclass). No headers, credentials or mapping (FR-005, FR-006).

## Tap

```text
Tap (protocol): record(record: TraceRecord) -> None   # may raise; only RequestTrace calls it (R3a)
NullTap:  records nothing; used when no leg is enabled (FR-001)
FileTap:  open(directory, ttl_s, uid) checks the directory (R6); record appends to the record's file (R4, R5);
          sweep(now) deletes expired files (R7)
```

## RequestTrace

One per traced request, created by the route wrapper (R8). The only tracing object handlers touch.

- Holds `tap`, enabled `legs`, `session_id`, `route`, `request_id`.
- `wants(leg) -> bool`: lets the handler skip work (and stream assembly) for a disabled leg.
- `capture(leg, body: bytes)`: keeps a body for the leg.
- `open_stream(leg)`: starts the leg's `MessageAssembler` when its stream starts, so a stream cut before any
  event is still recorded (empty, incomplete).
- `feed(leg, event)`: feeds a stream event to the leg's `MessageAssembler`.
- `upstream_responded(status)`: records Anthropic's status for the upstream legs.
- `finish(status)`: once; builds a `TraceRecord` for each captured or opened enabled leg in leg order and
  passes it to the tap.
- `feed` and the per-leg build-and-write in `finish` catch any exception, log leg and exception type, and drop
  that leg (R3a, FR-013).

## MessageAssembler

Fed `ServerSentEvent`s in order (R3); `message()` returns the assembled message, `complete` whether it saw
`message_stop` and no `error`. Pure: no I/O.

## Settings (config.py)

See [contracts/configuration.md](contracts/configuration.md). Validation fails startup before the port opens:
unknown leg name; (US2) a raw leg without `trace_raw`.
