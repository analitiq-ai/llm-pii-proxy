# Data Model: Payload traces per leg

## Leg

Enum of the legs in [spec.md § Legs](spec.md#legs). US1 defines `upstream_out` and `upstream_in`; US2 adds
`client_in` and `client_out` and the property `raw` (true for those two).

## TraceRecord

The one record type (FR-004). Serialized as in [contracts/trace-record.md](contracts/trace-record.md).

| Field | Type | Rule |
|---|---|---|
| `time` | datetime (UTC) | when the record is built |
| `session_id` | str \| None | None only for a request rejected for having none (US2) |
| `request_id` | str | R10; same for every leg of one request |
| `leg` | Leg | |
| `route` | str | request path, e.g. `/v1/messages` |
| `status` | int \| None | R8; None only for `upstream_out` when upstream was unreachable |
| `complete` | bool | false only for a streamed leg that was cut (FR-007) |
| `payload` | JSON value | the parsed JSON body; a body that is not JSON is recorded as its text, invalid UTF-8 replaced |

Immutable (frozen dataclass). No headers, credentials or mapping (FR-005, FR-006).

## Tap

```text
Tap (protocol): record(record: TraceRecord) -> None   # never raises (FR-013)
NullTap:  records nothing; used when no leg is enabled (FR-001)
FileTap(directory): appends to the file for record.session_id (R4, R5); sweep(now) deletes expired files (R7)
```

## RequestTrace

One per proxied request, created by the handler.

- Holds `tap`, enabled `legs`, `session_id`, `route`, `request_id`.
- `wants(leg) -> bool`: lets the handler skip building a payload (and skip stream assembly) for a disabled
  leg, so tracing off does no extra work.
- `record(leg, status, payload, complete=True)`: builds the `TraceRecord` and passes it to the tap when the leg
  is enabled.

## MessageAssembler

Fed `ServerSentEvent`s in order (R3); `message()` returns the assembled message so far. Pure: no I/O.

## Settings (config.py)

See [contracts/configuration.md](contracts/configuration.md). Validation fails startup before the port opens:
unknown leg name; (US2) a raw leg without `trace_raw`.
