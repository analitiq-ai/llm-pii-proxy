# Feature Specification: Trace viewer

**Feature Branch**: `16-spec`

**Created**: 2026-11-25

**Status**: Draft

**Input**: User description: "A local page that lists traced requests and, for a selected request, shows side by side what the proxy scrubbed before sending (`client_in` vs `upstream_out`) and what came back and how it was restored (`upstream_in` vs `client_out`), as collapsible JSON with the differences marked." Issue #16.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See what the proxy changed in a request (Priority: P1)

An operator has run the proxy with payload tracing on. They open the trace viewer in a browser on the
same machine and pick the trace directory. The viewer lists every traced request, newest first, with
its time, session, route, status and the legs it has. The operator selects one request and sees two
comparisons, each with the two payloads side by side as collapsible JSON trees:

- **Sent**: `client_in` (what Claude Code sent) against `upstream_out` (what went to the provider), so
  every value the proxy replaced with a placeholder stands out.
- **Received**: `upstream_in` (what the provider answered) against `client_out` (what Claude Code got),
  so every placeholder the proxy restored stands out.

Unchanged parts start collapsed and a "changed only" filter hides them, so in a long conversation the
operator lands on the replaced values without scrolling through the history.

**Why this priority**: it is the whole feature. Today the operator reads JSONL by hand and has to
match legs by request id and spot replacements inside long payloads.

**Independent Test**: write a trace directory with known records (a fully traced request, one with
only the scrubbed legs, a rejected one, a cut-short stream and a malformed line), open the viewer on
it and check the list, both comparisons and the reports against the records.

**Acceptance Scenarios**:

1. **Given** a trace directory holding requests from several sessions, **When** the operator picks
   it, **Then** every request appears once in the list, newest first, with its time, session (or "no
   session"), route, status and recorded legs.
2. **Given** a request with all four legs, **When** the operator selects it, **Then** the Sent
   comparison shows `client_in` and `upstream_out` side by side with each replaced value marked, and
   the Received comparison shows `upstream_in` and `client_out` side by side with each restored value
   marked.
3. **Given** a request whose `client_in` and `client_out` were not recorded (real-value legs off),
   **When** the operator selects it, **Then** each comparison shows the recorded payload on its own
   and labels the other side "not recorded", without marking anything as added or removed.
4. **Given** a request the proxy rejected before forwarding, **When** the operator selects it,
   **Then** the Sent comparison shows `client_in` with `upstream_out` "not recorded" and the
   Received comparison shows both sides "not recorded".
5. **Given** a leg recorded as incomplete (the stream was cut short), **When** the operator views
   the request, **Then** that leg is flagged incomplete in the list and in its comparison.
6. **Given** a trace file with lines that are not trace records, **When** the operator picks the
   directory, **Then** the viewer reports how many such lines each file has and still shows every
   valid record.
7. **Given** the viewer is open, **When** the proxy writes more records and the operator picks the
   directory again, **Then** the list includes the new requests.

### Edge Cases

- A directory with no trace files, or trace files with no records: the viewer says there are no
  traced requests.
- Two records for the same leg of one request: the viewer reports the request as having duplicate
  records and does not compare that leg's pair, rather than picking one.
- Payloads that differ in shape, not just in text (a field present on one side only): the viewer
  marks the field as added or removed, so nothing is hidden.
- Payload text containing markup: it is shown as text, never interpreted.
- Files in the directory that are not trace files, including trace files in subdirectories: ignored.
- A trace file that cannot be read (its permissions forbid it, or it changed after it was picked):
  the viewer names it as unreadable and still shows the records of the other files.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The viewer MUST read the trace files of a directory the operator picks, and nothing
  else.
- **FR-002**: The viewer MUST list each traced request once, grouped by request id across the legs,
  newest first, showing its time, session, route, status and which legs were recorded.
- **FR-003**: For a selected request the viewer MUST show the Sent comparison (`client_in` against
  `upstream_out`) and the Received comparison (`upstream_in` against `client_out`), each as two
  collapsible JSON trees side by side, with values that differ marked.
- **FR-004**: Each comparison MUST start with unchanged parts collapsed, and MUST offer a filter that
  hides unchanged parts.
- **FR-005**: A comparison with one leg missing MUST show the recorded leg's payload on its own and
  label the missing one "not recorded"; with both missing it MUST label both.
- **FR-006**: A leg recorded as incomplete MUST be flagged in the list and in its comparison.
- **FR-007**: Lines that are not valid trace records MUST be reported per file with their count, and
  MUST NOT prevent valid records from being shown.
- **FR-008**: A request with more than one record for the same leg MUST be reported, and that leg's
  comparison MUST NOT be shown.
- **FR-009**: The viewer MUST NOT send any data off the machine or load anything from the network, and
  MUST NOT copy trace content anywhere (no browser storage, no upload, no file written).
- **FR-010**: Payload content MUST be displayed as text and never interpreted as markup or code.
- **FR-011**: Picking the directory again MUST reload it, showing records written since.
- **FR-012**: The viewer MUST NOT change the proxy: its request path and its trace files stay as they
  are.

### Key Entities

- **Trace record**: one leg of one request, as the proxy writes it: time, session, request id, leg,
  route, status, whether it is complete, and the payload.
- **Traced request**: the records sharing a request id; up to one per leg.
- **Comparison**: a pair of legs of one request shown side by side: Sent (`client_in`,
  `upstream_out`) or Received (`upstream_in`, `client_out`).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From opening the viewer, an operator reaches the replaced values of a chosen request in
  under 30 seconds, without reading raw trace files.
- **SC-002**: Every replaced or restored value in a fully traced request is marked; none is shown as
  unchanged.
- **SC-003**: While the viewer is in use, it makes no network request to any host other than the
  machine it runs on.
- **SC-004**: A day of traces (24 hours, the default retention) from one active Claude Code user loads
  and lists in under 5 seconds.

## Assumptions

- The operator runs the viewer on the machine that holds the traces, as the user who owns them.
- A changed string is marked as a whole; marking the individual replaced words inside it is out of
  scope.
- Live updating while the proxy runs is out of scope; picking the directory again reloads it.
- The diff panels reuse an existing open-source JSON diff component (MIT licensed) rather than a new
  diff view; it is kept in the repo, not loaded from the network.
- The trace record format is the one the proxy writes today (#4); the viewer reads it and does not
  change it.
