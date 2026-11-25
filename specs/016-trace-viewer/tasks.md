# Tasks: Trace viewer

**Input**: Design documents from `specs/016-trace-viewer/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Required. Write each test task, run it, and confirm it fails for the stated reason before the task
that makes it pass.

**Organization**: one user story, US1 (#16).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: US1

## Rules that apply to every task

- The page loads nothing from the network and uses no storage API (FR-009, research R5).
- Payload text reaches the DOM only through the vendored component, which escapes it, or through
  `textContent` (FR-010). The viewer's own code never assigns payload text to `innerHTML`.
- `viewer/traces.js` touches no DOM, so Node can run it.
- Tests that write trace directories use `tmp_path`, which is outside any git tree.

---

## US1 block — issue #16

### Phase 1: Setup (Shared Infrastructure)

- [ ] T001 Vendor `json-diff-viewer-component` 0.6.4 into `viewer/vendor/json-diff-viewer/`: `viewer.js`, `diff.js`, `styles.js` from the npm package's `src/lib/` and its `LICENSE`, byte for byte (research R1). Record the version and the package's `sha512` integrity from `npm view json-diff-viewer-component@0.6.4 dist.integrity` in `viewer/vendor/json-diff-viewer/VERSION`.
- [ ] T002 Test in `tests/integration/test_viewer.py` that `node --test 'tests/viewer/*.test.js'` exits 0 and ran at least one test (its TAP summary `# tests N` with N > 0: a pattern matching no file passes with zero tests), run with `subprocess`; it fails, not skips, when `node` is missing or older than 22 (research R7). Red: zero tests ran.

### Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the pure module every part of the page uses (data-model.md).

- [ ] T003 Test `parseTraceFile(file, text)` in `tests/viewer/traces.test.js`: returns `{records, invalidLines}`; each record has `file`, `time`, `sessionId`, `requestId`, `leg`, `route`, `status`, `complete`, `line`; a line is valid only if it is a JSON object where "`time`: string that parses as a date", "`session_id`: string, or null", "`request_id`: non-empty string", "`leg`: one of `client_in`, `upstream_out`, `upstream_in`, `client_out`", "`route`: string", "`status`: integer", "`complete`: boolean", and `payload` is present with any JSON value, including a string; each other line, including one cut short and a JSON array, adds one to `invalidLines`; empty lines are neither. Red: module missing.
- [ ] T004 Test `groupRequests(records)` in `tests/viewer/traces.test.js`: one entry per `requestId` across files; `time` the earliest record time; `sessionId` and `route` from the earliest record; sorted newest first, ties by `requestId`; `legs` maps each leg with exactly one record to it; `duplicateLegs` lists legs with more than one. Red: function missing.
- [ ] T005 Test `comparisons(request)` in `tests/viewer/traces.test.js`: returns Sent (`client_in`, `upstream_out`) then Received (`upstream_in`, `client_out`); each side `recorded` with its record, `not recorded`, or `duplicate`; each comparison's `shownAs` is `diff` when both sides are recorded, `single` when one is recorded and the other not recorded, `none` when neither is recorded, `duplicate` when either side is duplicate; a recorded side carries `complete`. Red: function missing.
- [ ] T006 Test `payloadOf(record)` in `tests/viewer/traces.test.js`: returns the payload parsed from `record.line`, an object or a string as written. Red: function missing.
- [ ] T007 Implement `parseTraceFile`, `groupRequests`, `comparisons` and `payloadOf` in `viewer/traces.js` as an ES module with no DOM access; records keep the raw line, not the parsed payload (research R6). T002–T006 green.

### Phase 3: User Story 1 - See what the proxy changed in a request (Priority: P1) 🎯 MVP

**Goal**: list traced requests and show the Sent and Received comparisons for the selected one.

**Independent Test**: quickstart.md § Check by hand, on a directory with each case.

- [ ] T008 [US1] Test against the proxy's own writer in `tests/integration/test_viewer.py` (contracts/trace-input.md): write, with `FileTap.open(tmp_path, ...)` and `TraceRecord`, a request with all four legs and a session id that `FileTap` names its file by hashing (one with a space), one with only `upstream_out` and `upstream_in`, one with only `client_in` (status 400, payload the text `{`) and no session id, and one whose `upstream_in` and `client_out` have `complete` false; append a line `not json` to one file; run `node tests/viewer/summarize.js <dir>` and assert its JSON: four requests newest first with their legs, sessions as the records hold them (the spaced id, not its hash; `null` for the `_no-session.jsonl` one), each comparison's `shownAs`, the incomplete flags, and one invalid line reported for that file. Red: `summarize.js` missing.
- [ ] T009 [US1] Implement `tests/viewer/summarize.js`: read every `*.jsonl` at the top level of the directory given as its argument, run `parseTraceFile`, `groupRequests` and `comparisons` from `viewer/traces.js`, print requests, comparisons and file reports as JSON. T008 green.
- [ ] T010 [P] [US1] Implement the one-sided tree in `viewer/tree.js`: `renderTree(value)` returns a DOM node; objects and arrays as `<details>` whose children are built on first open; keys and values written with `textContent` only; a string payload shown as text (research R2).
- [ ] T011 [US1] Implement `viewer/index.html`: the CSP meta exactly as research R5; a directory input (`webkitdirectory`); areas for the file report, the request list and the comparisons; loads `app.js` as a module and nothing else.
- [ ] T012 [US1] Implement `viewer/app.js`: on each directory pick, read the top-level `*.jsonl` files (FR-001, FR-011), show the file report when any file has invalid lines (FR-007), the empty state when there are no records, and the request list with time, session or "no session", route and each recorded leg with its status, incomplete legs flagged (FR-002, FR-006); on selecting a request, render each comparison by `shownAs`: `diff` as a `json-diff-viewer` with `setData` of the two payloads, `single` as `renderTree` beside a "not recorded" label, `none` as two labels, `duplicate` as a report naming the leg (FR-003–FR-005, FR-008); flag incomplete sides.
- [ ] T013 [US1] Run quickstart.md § Check by hand in Chromium with the proxy's tracing on, every row, including the Network tab (SC-003) and a day of traces timed (SC-004); fix what fails, with a test first where the failure is in `viewer/traces.js`.

### Phase 4: Polish & Cross-Cutting Concerns

- [ ] T014 [P] README: a "Trace viewer" paragraph after the payload tracing settings: the serve command bound to `127.0.0.1`, picking the directory, the two comparisons, and that the page cannot send data.
- [ ] T015 [P] CLAUDE.md: the tests need Node 22, run by `uv run pytest`.
- [ ] T016 Run `uv run pytest` and the lint command in CLAUDE.md; all green.

---

## Dependencies & Execution Order

- T001 and T002 first; T003–T006 (one file, so in sequence, all red before T007), then T007.
- T008 then T009. T010 in parallel with T008–T009. T011 then T012, after T007 and T010.
- T013 after T012. T014 and T015 any time after T012; T016 last.

## Parallel Example

```text
T010 alongside T008      # different files, no shared dependency
```

## Implementation Strategy

One story, one PR: Setup and Foundational give the tested pure module; US1 adds the pinned writer
test and the page; Polish documents it. The page is not usable before T012, so there is no earlier
increment worth shipping.
