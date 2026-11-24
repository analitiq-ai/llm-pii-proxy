# Feature Specification: Payload traces per leg

**Feature Branch**: `4-spec`

**Created**: 2026-11-24

**Status**: Draft

**Input**: Issue #4. See what goes into the proxy, what it sends to Anthropic, what Anthropic sends back, and
what the proxy returns to the client. Off by default, switchable per leg, local files first; other outputs
(for example a self-hosted trace UI) later behind the same recording interface.

## Legs

| Leg | Name | Direction | Holds |
|---|---|---|---|
| 1 | `client_in` | Claude Code → proxy | real values |
| 2 | `upstream_out` | proxy → Anthropic | placeholders |
| 3 | `upstream_in` | Anthropic → proxy | placeholders |
| 4 | `client_out` | proxy → Claude Code | real values |

Legs 2 and 3 are the **scrubbed legs**; legs 1 and 4 are the **raw legs**.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See exactly what Anthropic received and returned (Priority: P1)

An operator turns tracing on for the scrubbed legs, works in Claude Code as usual, then opens the session's
trace file and reads, request by request, the body Anthropic received and the message it returned. They can
check that nothing sensitive went out, and see how Claude responded to the placeholders.

**Why this priority**: It answers the question the proxy exists for — did anything leak — and writes no real
values to disk beyond what already left the machine.

**Independent Test**: Enable `upstream_out` and `upstream_in`, send a request with a planted email through the
proxy against a mocked Anthropic, and read the trace file: two records, linked by one request id, holding the
placeholder and not the email.

**Acceptance Scenarios**:

1. **Given** tracing is off (the default), **When** requests pass through the proxy, **Then** no trace file is
   created and nothing is written to the trace directory.
2. **Given** `upstream_out` and `upstream_in` are enabled, **When** a non-streamed request succeeds, **Then** the
   session's trace file gains one `upstream_out` record holding the body as forwarded, and one `upstream_in`
   record holding the response body as received, with the same request id.
3. **Given** the same, **When** the response is streamed, **Then** the `upstream_in` record holds the message
   assembled from the stream, written once the stream ends.
4. **Given** only `upstream_out` is enabled, **When** a request succeeds, **Then** only the `upstream_out`
   record is written.
5. **Given** tracing is on, **When** the proxy starts, **Then** it logs a warning naming the enabled legs and the
   trace directory.
6. **Given** the trace directory already exists with a mode other than 0700 or another owner, or holds a trace
   file that is not 0600, **When** the proxy starts, **Then** it exits before listening, with an error naming the
   path and its actual mode or owner.
7. **Given** a trace file older than the retention period, **When** the proxy starts or the retention sweep
   runs, **Then** the file is deleted.

---

### User Story 2 - Compare what came in with what went out (Priority: P2)

An operator investigating a miss or a broken restore also turns on the raw legs, with a second explicit
opt-in, and compares leg 1 with leg 2 (what the proxy replaced) and leg 3 with leg 4 (what it restored).

**Why this priority**: Needed for debugging detection and restoration, but it writes real PII to disk, so it is
opt-in on top of opt-in.

**Independent Test**: Enable all four legs with the raw opt-in, send a request with a planted email, and check
the file holds leg 1 with the email, leg 2 with its placeholder, and legs 3 and 4 likewise, all under one
request id. Then select a raw leg without the opt-in and check the proxy refuses to start.

**Acceptance Scenarios**:

1. **Given** a raw leg is selected and the raw opt-in is not set, **When** the proxy starts, **Then** it exits
   with an error naming the raw legs and the opt-in, before listening.
2. **Given** all four legs and the raw opt-in, **When** a request succeeds, **Then** four records share one
   request id, in the order `client_in`, `upstream_out`, `upstream_in`, `client_out`.
3. **Given** `client_in` is enabled, **When** a request is rejected before forwarding (fail closed), **Then** the
   `client_in` record is written with the rejection status, and no other leg is.
4. **Given** the raw legs are enabled, **When** the proxy starts, **Then** the startup warning says real values
   are being written to disk.

---

### Edge Cases

- **Trace write fails** (disk full, permissions changed): the request proceeds unchanged; the proxy logs the
  failure kind and the leg, never the content.
- **Client disconnects mid-stream**: the `upstream_in` and `client_out` records hold what was assembled so far
  and are marked incomplete.
- **Request without a session id**: the proxy already rejects it; a `client_in` record, if enabled, goes to a
  file for requests with no session.
- **Upstream error** (4xx/5xx from Anthropic): `upstream_in` records the error body; `client_out` records what
  the client received.
- **Routes with no content** (`/v1/models`, `/api/hello`): not traced.
- **Trace directory inside a git working tree**: startup fails, so traces can't be committed by accident.
- **Trace directory does not exist**: created with owner-only permissions.
- **Trace directory or a trace file exists with looser permissions or another owner**: startup fails, naming the
  path and what is wrong. The proxy does not fix permissions itself.
- **Concurrent requests in one session**: records from different requests may interleave in the file; each is
  one complete line, and the request id links them.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Tracing MUST be off by default. When off, the proxy MUST write nothing to the trace directory and
  MUST behave exactly as without this feature.
- **FR-002**: The operator MUST be able to choose any subset of the four legs.
- **FR-003**: Selecting a raw leg MUST require a separate explicit opt-in. Without it, startup MUST fail before
  the proxy listens.
- **FR-004**: A trace record MUST hold exactly: time; session id (absent only for a request rejected for having
  none); request id, unique per proxied request; leg; route; HTTP status; complete or incomplete (incomplete
  only for a stream that was cut, FR-007); and the payload as it crossed that leg. This is the record's one
  definition.
- **FR-005**: Records MUST NOT hold credentials (`authorization`, `x-api-key`, cookies) or any request or
  response header other than the status.
- **FR-006**: The vault mapping MUST NOT be written as such. (With both a raw and a scrubbed leg enabled, the
  pairing can be inferred; that is the purpose of the raw opt-in.)
- **FR-007**: A streamed response MUST be recorded as one assembled message per leg, written when the stream
  ends or is cut, marked incomplete in the latter case.
- **FR-008**: Records MUST be written as one JSON object per line, one file per session, under the trace
  directory.
- **FR-009**: Startup MUST fail if the trace directory is inside a git working tree.
- **FR-010**: The trace directory MUST be owner-only (0700) and trace files owner read/write only (0600). An
  existing directory or file that is looser, or owned by another user, MUST fail startup.
- **FR-011**: Trace files older than the retention period (default 24 hours) MUST be deleted at startup and
  periodically while running.
- **FR-012**: Each startup with tracing on MUST log a warning naming the enabled legs and the directory, and
  stating when real values are being written.
- **FR-013**: Tracing MUST NOT change what is forwarded or returned, and a failure to record MUST NOT fail or
  delay the request beyond the write attempt. The failure is logged without content.
- **FR-014**: A request rejected before forwarding MUST still produce its `client_in` record when that leg is
  enabled.
- **FR-015**: Recording MUST go through one interface that takes a record and does not know where it goes, so
  further outputs can be added without changing the proxy's request handling. Only the local-file output is
  in scope.
- **FR-016**: The proxy's own log MUST continue to hold no request or response bodies; bodies go only to trace
  records.

### Key Entities

- **Trace record**: one leg of one request, with the fields in FR-004.
- **Trace output**: where records go. The local-file output writes one JSONL file per session; a disabled
  configuration uses an output that records nothing.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With tracing off, a full test run writes zero files to the trace directory.
- **SC-002**: With all four legs on, 100% of proxied requests in a session have one record per enabled leg,
  linked by request id.
- **SC-003**: Selecting a raw leg without the opt-in fails startup in 100% of attempts, before any request is
  accepted.
- **SC-004**: Forwarded and returned bytes are identical with tracing on and off, for the same input.
- **SC-005**: Median added latency per request with all legs on stays under 5 ms for a 100 KB request.
- **SC-006**: A simulated write failure leaves 100% of requests succeeding.

## Assumptions

- The operator is the only user of the machine account that runs the proxy; owner-only file permissions are the
  access control.
- Retention by age is enough; no size cap in this version.
- Payloads are recorded in full; there is no truncation or sampling.
- No viewer or diff tool ships with this feature; JSONL is read with standard tools (`jq`).
- Event-level stream traces are out of scope; the assembled message is enough to see what was said.
- A trace UI output (for example a self-hosted Langfuse) is a later feature that adds an output behind FR-015.
