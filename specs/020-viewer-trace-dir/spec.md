# Feature Specification: Trace viewer reads the configured trace directory

**Feature Branch**: `20-spec`

**Created**: 2026-11-25

**Status**: Draft

**Input**: User description: "The viewer asks me to select a directory. The app knows the directory into which it is writing the traces; the UI should read it from the configuration, not ask the user for it." Issue #20.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Open the viewer on the proxy's traces (Priority: P1)

An operator has run the proxy with payload tracing on. They start the trace viewer with one command,
using the same configuration as the proxy, and open the address it prints. The viewer shows the
traced requests from the proxy's configured trace directory straight away, with no directory to pick.
When the proxy has written more records, the operator reloads and sees them.

Everything the viewer shows once it has the records (the request list, the Sent and Received
comparisons, the reports of incomplete legs, duplicate records, unreadable files and invalid lines)
stays as it is today.

**Why this priority**: it is the whole feature. Today the operator must find and pick the directory the
proxy already knows, on every visit and again for every refresh, and a wrong pick silently shows
another directory's traces or none.

**Independent Test**: configure a trace directory holding known records, start the viewer with that
configuration, open the printed address, and check that the requests appear without any picking;
append a record, reload, and check that it appears.

**Acceptance Scenarios**:

1. **Given** a configured trace directory holding requests from several sessions, **When** the
   operator starts the viewer and opens the printed address, **Then** the request list shows every
   request in that directory, with no directory picker on the page.
2. **Given** the viewer is open, **When** the proxy writes more records and the operator reloads,
   **Then** the list includes the new requests.
3. **Given** the trace directory is set to a non-default location in the proxy's configuration,
   **When** the operator starts the viewer with that configuration, **Then** the viewer shows that
   directory's traces, and no setting other than the proxy's own names the directory.
4. **Given** the viewer is running, **When** a request reaches it without the access key from the
   printed address, or addressed to any host name other than the loopback address it listens on,
   **Then** it is refused and no trace content is returned.
5. **Given** the viewer is running, **When** anything other than the viewer's own page files or the
   configured directory's trace files is requested, **Then** it is refused.

### Edge Cases

- The configured trace directory does not exist, or fails the checks the proxy applies to it (inside a
  git working tree, not owner-only, owned by someone else): the viewer refuses to start and names the
  directory and the reason, rather than showing an empty list.
- The configuration is invalid in any way the proxy would refuse: the viewer refuses to start with the
  same error.
- The directory exists and holds no trace records: the viewer says there are no traced requests.
- The proxy is writing a record while the viewer reads: a line cut short is reported as an invalid line,
  as today; a reload once the write has finished shows the record.
- A trace file is deleted between listing and reading (retention ran): the viewer names it unreadable
  and still shows the other files' records, as today.
- The configured address is not a loopback IP address: the viewer refuses to start, as the proxy does.
- The chosen port is in use: the viewer refuses to start and names the port.
- Tracing is off in the configuration: the viewer still starts and shows whatever the directory holds;
  whether the proxy is tracing is not the viewer's concern.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The viewer MUST read the trace files of the trace directory set in the proxy's
  configuration, and nothing else. It MUST NOT ask the operator for a directory.
- **FR-002**: The viewer MUST take its configuration from the same source and with the same validation
  as the proxy, so the trace directory is defined once.
- **FR-003**: The viewer MUST refuse to start when the configured trace directory is missing or fails
  the proxy's safety checks for it, naming the directory and the reason.
- **FR-004**: Opening or reloading the page MUST load the directory's current records.
- **FR-005**: The viewer MUST listen only on a loopback IP address, under the same rule as the proxy,
  on a configurable port.
- **FR-006**: Each start of the viewer MUST create a fresh, unguessable access key, print the address
  that carries it, and refuse every request for trace content that does not carry it.
- **FR-007**: The viewer MUST refuse every request whose host is anything other than the loopback
  address and port it listens on.
- **FR-008**: The viewer MUST serve only its own page files and the trace files of the configured
  directory. No request can choose a file path.
- **FR-009**: The page MUST NOT send data to, or load anything from, any origin other than the viewer
  itself, and MUST NOT copy trace content anywhere (no browser storage, no upload, no file written).
- **FR-010**: Payload content MUST be displayed as text and never interpreted as markup or code.
- **FR-011**: The viewer MUST NOT change the proxy's request path or its trace files.
- **FR-012**: The viewer MUST NOT write the access key or any trace content to its log.

### Key Entities

- **Trace directory**: the directory set in the proxy's configuration, where the proxy writes one trace
  file per session. Its location and safety checks are defined once, by the proxy's configuration.
- **Access key**: a random secret created at each viewer start, carried in the printed address; it
  limits reading the traces to whoever was handed that address.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From running the start command, an operator sees the request list in under 10 seconds,
  with zero directory choices to make.
- **SC-002**: Seeing records written since the page was opened takes one action (a reload).
- **SC-003**: Requests to the viewer without the access key, or addressed by any other host name,
  return no trace content in 100% of tested cases.
- **SC-004**: While the viewer is in use, the page makes no network request to any origin other than
  the viewer itself.
- **SC-005**: A day of traces (24 hours, the default retention) from one active Claude Code user loads
  and lists in under 5 seconds, as today.

## Assumptions

- The operator runs the viewer on the machine that holds the traces, as the user who owns them, with
  the proxy's configuration in the environment.
- Other users and processes on the same machine can reach a loopback port; the access key, not the
  loopback address, is what keeps them from the traces.
- Live updating while the page is open is out of scope; reloading shows new records.
- The page stays a single page with no build step, and keeps the vendored diff component; only how it
  gets the trace files changes.
- The trace record format is the one the proxy writes today; the viewer reads it and does not change it.
- Configuration stays environment-based; a configuration file is a separate change.
