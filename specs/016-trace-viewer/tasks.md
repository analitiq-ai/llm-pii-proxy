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

- [ ] T003 Test `traceFiles(paths)` in `tests/viewer/traces.test.js`: given paths relative to the picked directory's parent, as a directory input gives them (`traces/a.jsonl`), returns the trace files as `{path, name}`, sorted by name (data-model.md § Trace files): given `traces/b.jsonl`, `traces/sub/c.jsonl`, `traces/notes.txt`, `traces/a.jsonl.bak` and `traces/a.jsonl`, in that order, it returns exactly `{path: "traces/a.jsonl", name: "a.jsonl"}` then `{path: "traces/b.jsonl", name: "b.jsonl"}`; no paths returns none. Red: module missing.
- [ ] T004 Test `parseTraceFile(file, text)` in `tests/viewer/traces.test.js`: returns `{records, invalidLines}`; each record has `file`, `time`, `sessionId`, `requestId`, `leg`, `route`, `status`, `complete`, `line`; a line is valid only if it is a JSON object where "`time`: string that parses as a date", "`session_id`: string, or null", "`request_id`: non-empty string", "`leg`: one of `client_in`, `upstream_out`, `upstream_in`, `client_out`", "`route`: string", "`status`: integer", "`complete`: boolean", and `payload` is present with any JSON value (cases: object, array, string, number, `null`); each other line, including one cut short and a JSON array, adds one to `invalidLines`; empty lines are neither. Red: function missing.
- [ ] T005 Test `groupRequests(records)` in `tests/viewer/traces.test.js`: one entry per `requestId` across files; `time` the earliest record time; `sessionId` and `route` from the earliest record; sorted newest first, ties by `requestId`; `legs` maps each leg with exactly one record to it; `duplicateLegs` lists legs with more than one. Red: function missing.
- [ ] T006 Test `comparisons(request)` in `tests/viewer/traces.test.js`: returns Sent (`client_in`, `upstream_out`) then Received (`upstream_in`, `client_out`); each side `recorded` with its record, `not recorded`, or `duplicate`; each comparison's `shownAs` is `diff` when both sides are recorded, `single` when one is recorded and the other not recorded, `none` when neither is recorded, `duplicate` when either side is duplicate; a recorded side carries `complete`. Red: function missing.
- [ ] T007 Test `payloadOf(record)` in `tests/viewer/traces.test.js`: returns the payload parsed from `record.line`, whatever JSON value it is (cases: object, array, string, `null`). Red: function missing.
- [ ] T008 Implement `traceFiles`, `parseTraceFile`, `groupRequests`, `comparisons` and `payloadOf` in `viewer/traces.js` as an ES module with no DOM access; records keep the raw line, not the parsed payload (research R6). T002–T007 green.

### Phase 3: User Story 1 - See what the proxy changed in a request (Priority: P1) 🎯 MVP

**Goal**: list traced requests and show the Sent and Received comparisons for the selected one.

**Independent Test**: quickstart.md § Check by hand, on a directory with each case.

- [ ] T009 [US1] Test against the proxy's own writer in `tests/integration/test_viewer.py` (contracts/trace-input.md): write, with `FileTap.open(tmp_path, ...)` and `TraceRecord`, a request with all four legs and a session id that `FileTap` names its file by hashing (one with a space), one with only `upstream_out` and `upstream_in`, one with only `client_in` (status 400, payload the text `{`) and no session id, and one whose `upstream_in` and `client_out` have `complete` false; append a line `not json` to one file; add `sub/nested.jsonl` holding a valid record of a fifth request, and `notes.txt`; run `node tests/viewer/summarize.js <dir>` and assert its JSON: the four requests newest first and not the fifth, with their legs, sessions as the records hold them (the spaced id, not its hash; `null` for the `_no-session.jsonl` one), each comparison's `shownAs`, the incomplete flags, and one invalid line reported for that file under its name, without the directory. Red: `summarize.js` missing.
- [ ] T010 [US1] Implement `tests/viewer/summarize.js`: list the directory given as its argument recursively, as paths prefixed with the directory's name (the form a directory input gives), keep those `traceFiles` returns, run `parseTraceFile` on each with its `name`, `groupRequests` and `comparisons` from `viewer/traces.js`, print requests, comparisons and file reports as JSON. T009 green.
- [ ] T011 [P] [US1] Implement the one-sided tree in `viewer/tree.js`: `renderTree(value)` returns a DOM node for any JSON value; objects and arrays as `<details>` whose children are built on first open; keys and values written with `textContent` only; a number, boolean or `null`, at the root or inside, shown as its JSON text; a string at the root shown as its text (research R2).
- [ ] T012 [US1] Implement `viewer/index.html`: the CSP meta exactly as research R5; a directory input (`webkitdirectory`); areas for the file report, the request list and the comparisons; loads `app.js` as a module and nothing else.
- [ ] T013 [US1] Implement `viewer/app.js`. On each pick (research R4): clear the previous list, selection and file report; keep the files whose `webkitRelativePath` is a `path` `traceFiles` returns (FR-001); read each with its own `text()` call straight away, and on a failed read put the file's `name` in the file report with the error's `name` and go on with the others; run `parseTraceFile` on each text read, with the file's `name`; then set the input's value to `""` so the next pick of the same directory is a new pick (FR-011). Only the latest pick may change the page: each pick takes the next number, and the results of its reads are used only while it is still the latest. Show the file report when any file has invalid lines or failed to read (FR-007), the empty state when there are no records, and the request list with time, session or "no session", route and each recorded leg with its status, incomplete legs flagged (FR-002, FR-006). On selecting a request, render each comparison by `shownAs`: `diff` as a `json-diff-viewer` with `setData` of the two payloads, `single` as `renderTree` beside a "not recorded" label, `none` as two labels, `duplicate` as a report naming the leg (FR-003–FR-005, FR-008); flag incomplete sides.
- [ ] T014 [US1] Run quickstart.md § Check by hand in Chromium with the proxy's tracing on, every row, including the Network tab (SC-003) and a day of traces timed (SC-004); fix what fails, with a test first where the failure is in `viewer/traces.js`.

### Phase 4: Polish & Cross-Cutting Concerns

- [ ] T015 [P] README: a "Trace viewer" paragraph after the payload tracing settings: the serve command bound to `127.0.0.1`, picking the directory, the two comparisons, and that the page cannot send data.
- [ ] T016 [P] CLAUDE.md: add a line saying `uv run pytest` now runs Node tests too, so it needs Node 22 or later.
- [ ] T017 Run `uv run pytest` and the lint command in CLAUDE.md; all green.

---

## Dependencies & Execution Order

- T001 and T002 first; T003–T007 (one file, so in sequence, all red before T008), then T008.
- T009 then T010. T011 in parallel with T009–T010. T012 then T013, after T008 and T011.
- T014 after T013. T015 and T016 any time after T013; T017 last.

## Parallel Example

```text
T011 alongside T009      # different files, no shared dependency
```

## Implementation Strategy

One story, one PR: Setup and Foundational give the tested pure module; US1 adds the pinned writer
test and the page; Polish documents it. The page is not usable before T013, so there is no earlier
increment worth shipping.
