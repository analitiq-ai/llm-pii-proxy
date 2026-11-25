# Contract: trace record line

Each line of a trace file is one JSON object, UTF-8, ending in `\n`. Field rules are in
[data-model.md § TraceRecord](../data-model.md#tracerecord).

```json
{"time":"2026-11-24T10:15:02.123Z","session_id":"3f2b…","request_id":"9c41…","leg":"upstream_out","route":"/v1/messages","status":200,"complete":true,"payload":{"model":"…","messages":[…]}}
```

- Keys appear in the order above.
- `time` is ISO 8601 UTC with milliseconds and a `Z` suffix.
- `status` is always an integer: on upstream legs Anthropic's status, or 502 when Anthropic was unreachable
  (research R8).
- A streamed `upstream_in` or `client_out` payload has the shape of a non-streamed Messages API response, plus
  an `error` key when the stream carried an `error` event (research R3).

## Files

Under the trace directory (FR-008, research R5):

| File | Holds |
|---|---|
| `<session id>.jsonl`, or `<sha256 of session id>.jsonl` | records of that session |
| `_no-session.jsonl` (US2) | `client_in` records of requests rejected for having no session id |

Read with standard tools, e.g. `jq -c 'select(.request_id=="…")' <file>`.
