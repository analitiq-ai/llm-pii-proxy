# Research: Payload traces per leg

## R1. Output: local JSONL first

- **Decision**: one JSON object per line, appended to local files (FR-008).
- **Rationale**: no new dependency or service; `jq` reads it; matches the user's "simple first".
- **Alternatives**: LangSmith (cloud only unless Enterprise: sends real values off the machine, rejected);
  self-hosted Langfuse (a UI, but a service to run; deferred behind `Tap`, R9); SQLite (queryable, but needs a
  viewer and gains nothing over `jq` at this size).

## R2. Capturing streamed legs

- **Decision**: the handler feeds each upstream event to the request's trace before restoring it (for
  `upstream_in`). For `client_out` (US2), `restore_stream` yields `ServerSentEvent`s instead of bytes and
  `app.py` encodes them, so the restored events are fed the same way. One `MessageAssembler` serves both legs.
- **Rationale**: both legs are the same operation, assembling Anthropic stream events into a message, so one
  implementation serves both. Yielding events keeps the restored events available without parsing the
  encoded bytes back.
- **Alternatives**: parse the outgoing bytes again for `client_out` (a second SSE parser, rejected); record raw
  events (the spec excludes event-level traces); buffer the whole stream (forbidden: Claude Code's idle timeout,
  see `sse.py`).

## R3. Assembling a message

- **Decision**: follow the stream's own structure. `message_start` gives the message; `content_block_start`
  adds a block; deltas append (`text_delta` → `text`, `thinking_delta` → `thinking`, `signature_delta` →
  `signature`, `input_json_delta` → accumulated JSON parsed into `input` at `content_block_stop`);
  `message_delta` merges `stop_reason`, `stop_sequence` and `usage`. An `error` event's data is kept under a
  top-level `error` key. Other events and delta types are ignored. A tool input whose block never stopped, or
  whose JSON does not parse, stays as the accumulated string in `input`.
- **Complete** means the assembler saw `message_stop` and no `error` event. A stream that ends cleanly without
  `message_stop` (upstream truncation), is cut by the client, or carries an `error` event is recorded
  `complete: false` (FR-007).
- **Rationale**: the same shape as a non-streamed response, so legs compare with one mental model; deriving
  completeness from the terminal event covers every side that can cut a stream.

## R3a. One guard for all trace-side work (FR-013)

- **Decision**: handlers touch tracing only through `RequestTrace`. Feeding a stream event, and building and
  writing each leg's record in `finish`, catch any exception, log the leg and the exception type, and stop
  tracing that leg for the request. `Tap.record` is called only inside that guard, so a tap may raise.
  Capturing a body, opening a stream and noting the upstream status only store a value and are not guarded.
- **Rationale**: the work that can raise (assembler JSON parsing while the stream runs; payload decoding and
  the write in `finish`) is all inside the guard; a guard on the output alone would let an assembler bug cut
  the client's stream.

## R4. Writing records

- **Decision**: synchronous append per record: `os.open(path, O_WRONLY|O_APPEND|O_CREAT|O_NOFOLLOW, 0o600)`,
  write the encoded line, close. A write that returns fewer bytes than the line raises, like an `OSError`;
  `RequestTrace` logs and drops it (R3a).
- **Rationale**: the proxy runs on one event loop, so each record's write completes before another starts;
  lines never interleave. A 100 KB write to a local file is well under SC-005's 5 ms. `O_NOFOLLOW` stops a
  planted symlink redirecting real values. Checking the count stops a partial line joining the next record.
- **Alternatives**: a writer thread with a queue (ordering and shutdown flushing to get right, for no measured
  need); keeping files open per session (descriptor lifetime tied to session expiry, more state).

## R5. File names

- **Decision**: `<session id>.jsonl` when the id matches `[A-Za-z0-9-]{1,128}` (Claude Code's are UUIDs);
  otherwise the SHA-256 hex of the id. Records with no session id go to `_no-session.jsonl` (US2), a name no
  session id can produce.
- **Rationale**: the header is client input, so it never reaches a path unchecked; the common case stays
  readable.

## R6. Directory checks (FR-009, FR-010)

- **Decision**: at startup, before the port opens (`build_app`), when any leg is enabled:
  1. resolve the path; walk it and its parents for a `.git` entry (file or directory); found → fail;
  2. create the directory with mode 0700 if missing (parents with defaults);
  3. require mode exactly 0700 and owner = the process uid; each `*.jsonl` in it exactly 0600, same owner.
  The check takes the uid as a parameter so tests can exercise "another owner" without root.
- **Rationale**: matches the existing "everything that can fail on configuration fails before the port is
  opened"; walking for `.git` needs no `git` binary.

## R7. Retention (FR-011)

- **Decision**: delete `*.jsonl` whose mtime is older than `trace_ttl_h`, at startup and then hourly from a
  task started in the lifespan and cancelled at shutdown. A file that can't be deleted is logged (its path
  and the exception type) and the sweep goes on to the rest, then fails. A failed periodic sweep is logged
  (exception type) and the loop goes on; a failed startup sweep fails startup.
- **Rationale**: mtime is "last written", so an active session's file is never removed mid-session.

## R8. Status, order and every exit

- **Decision**: every record carries a status. `client_in` and `client_out` carry the status the client
  received. `upstream_out` and `upstream_in` carry Anthropic's status; when no response came (upstream
  unreachable) they carry the status the proxy returned (502), as the client legs do. Decided on #4.
- **One exit**: handlers only *capture* bodies into the request's `RequestTrace`. One wrapper around each
  traced route calls `finish(status)` on every exit: a response (forwarded upstream errors included), a
  `ProxyError` (its status), any other exception (500, re-raised). For a streamed response the wrapper defers
  `finish` until the response ends, however it ends. The response runs its end steps (finish the trace, close
  the body, close the upstream connection, log) in its own call: a step in the body's `finally` would not do,
  since on a client disconnect Starlette leaves the body to the garbage collector, and a body not yet started
  has no `finally` to run. A streamed leg is opened when its stream starts, so a stream cut before any event
  is still recorded, empty and `complete: false` (FR-007). `finish` runs once and writes each captured, enabled leg in leg
  order (`client_in`, `upstream_out`, `upstream_in`, `client_out`).
- **Rationale**: FR-004 requires a status on every record, SC-002 a record per enabled leg the request
  crossed. Recording at one exit instead of per return path means a new return path can't skip a leg.
- **Known gap**: a request cancelled before its handler returns writes nothing.

## R9. Later outputs

- **Decision**: a later output implements `Tap.record(TraceRecord)`; selected by a setting; nothing in
  `app.py` changes. A Langfuse output must target a loopback URL (README: loopback only, mapping stays local).
- **Rationale**: FR-015.

## R10. Request id

- **Decision**: `uuid4().hex` per proxied request; recorded only, never added to the response.
- **Rationale**: adding a header would change what the client receives (SC-004).
