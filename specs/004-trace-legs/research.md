# Research: Payload traces per leg

## R1. Output: local JSONL first

- **Decision**: one JSON object per line, appended to local files (FR-008).
- **Rationale**: no new dependency or service; `jq` reads it; matches the user's "simple first".
- **Alternatives**: LangSmith (cloud only unless Enterprise: sends real values off the machine, rejected);
  self-hosted Langfuse (a UI, but a service to run; deferred behind `Tap`, R9); SQLite (queryable, but needs a
  viewer and gains nothing over `jq` at this size).

## R2. Capturing streamed legs

- **Decision**: `restore_stream` yields `ServerSentEvent`s instead of bytes; `app.py` encodes them. One
  `MessageAssembler` is fed the upstream events (for `upstream_in`) and the restored events (for
  `client_out`).
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
  `message_delta` merges `stop_reason`, `stop_sequence` and `usage`. Unknown events and delta types are
  ignored. A tool input whose block never stopped stays as the accumulated string in `input`.
- **Rationale**: produces the same shape as a non-streamed response, so a reader compares legs with one
  mental model.

## R4. Writing records

- **Decision**: synchronous append per record: `os.open(path, O_WRONLY|O_APPEND|O_CREAT|O_NOFOLLOW, 0o600)`,
  one `os.write` of the encoded line, close. Errors (`OSError`, encoding) are caught in `FileTap.record`,
  logged as leg and exception type only, and dropped (FR-013).
- **Rationale**: the proxy runs on one event loop, so each record's single write completes before another
  starts; lines never interleave. A 100 KB write to a local file is well under SC-005's 5 ms. `O_NOFOLLOW`
  stops a planted symlink redirecting real values.
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
  task started in the lifespan and cancelled at shutdown.
- **Rationale**: mtime is "last written", so an active session's file is never removed mid-session.

## R8. Status per leg

- **Decision**: `client_in` and `client_out` carry the status the client received; `upstream_out` and
  `upstream_in` the status Anthropic returned. `upstream_out` has `null` status when upstream was unreachable.
  Records for a request are written once the status they carry is known: `client_in` and `upstream_out` when
  the upstream response headers arrive (or the request is rejected), `upstream_in` and `client_out` when the
  body or stream ends.
- **Rationale**: FR-004 requires a status on every record; this keeps the order of US2 scenario 2.

## R9. Later outputs

- **Decision**: a later output implements `Tap.record(TraceRecord)`; selected by a setting; nothing in
  `app.py` changes. A Langfuse output must target a loopback URL (README: loopback only, mapping stays local).
- **Rationale**: FR-015.

## R10. Request id

- **Decision**: `uuid4().hex` per proxied request; recorded only, never added to the response.
- **Rationale**: adding a header would change what the client receives (SC-004).
