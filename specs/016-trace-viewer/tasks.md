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
- Payload text is never interpreted as markup (FR-010). Three parts hold that, each checked once:
  - the vendored component escapes all text: it stays the files that were read, pinned by sha256 (T001);
  - the viewer's own code writes text with `textContent` and never uses `innerHTML`, `outerHTML`,
    `insertAdjacentHTML` or `document.write` (T012);
  - the browser shows markup in a payload as text: the quickstart row with `<img onerror>` (T016).
- `viewer/traces.js` touches no DOM, so Node can run it.
- Tests that write trace directories use `tmp_path`, which is outside any git tree.

---

## US1 block — issue #16

### Phase 1: Setup (Shared Infrastructure)

- [ ] T001 Test in `tests/integration/test_viewer.py` that the vendored component is the pinned one (research R1): `viewer/vendor/json-diff-viewer/VERSION` holds `version 0.6.4`, the npm `integrity` it was checked against, and one line `<sha256>  <file>` for each of exactly `viewer.js`, `diff.js`, `styles.js` and `LICENSE`; for each, `scripts/sha256-matches.sh <file> <sha256>` exits 0, and the failure message says mismatch on exit 1 and tool failure on exit 2. Red: `VERSION` missing.
- [ ] T002 Vendor `json-diff-viewer-component` 0.6.4 into `viewer/vendor/json-diff-viewer/`: download the tarball with `npm pack json-diff-viewer-component@0.6.4`, check its sha512 against `npm view json-diff-viewer-component@0.6.4 dist.integrity`, and copy `viewer.js`, `diff.js`, `styles.js` from its `src/lib/` and its `LICENSE` byte for byte; write `VERSION` with the version, that integrity and each copied file's sha256. T001 green.
- [ ] T003 Test in `tests/integration/test_viewer.py` that `node --test 'tests/viewer/*.test.js'` exits 0 and ran at least one test (its TAP summary `# tests N` with N > 0: a pattern matching no file passes with zero tests), run with `subprocess`; it fails, not skips, when `node` is missing or older than 22 (research R7). Red: zero tests ran.

### Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the pure module every part of the page uses (data-model.md).

- [ ] T004 Test `traceFiles(paths)` in `tests/viewer/traces.test.js`: given paths relative to the picked directory's parent, as a directory input gives them (`traces/a.jsonl`), returns the trace files as `{path, name}`, sorted by name (data-model.md § Trace files): given `traces/b.jsonl`, `traces/sub/c.jsonl`, `traces/notes.txt`, `traces/a.jsonl.bak` and `traces/a.jsonl`, in that order, it returns exactly `{path: "traces/a.jsonl", name: "a.jsonl"}` then `{path: "traces/b.jsonl", name: "b.jsonl"}`; no paths returns none. Red: module missing.
- [ ] T005 Test `parseTraceFile(file, text)` in `tests/viewer/traces.test.js`: returns `{records, invalidLines}`; each record has `file`, `time`, `sessionId`, `requestId`, `leg`, `route`, `status`, `complete`, `line`; a line is valid only if it is a JSON object where "`time`: string that parses as a date", "`session_id`: string, or null", "`request_id`: non-empty string", "`leg`: one of `client_in`, `upstream_out`, `upstream_in`, `client_out`", "`route`: string", "`status`: integer", "`complete`: boolean", and `payload` is present with any JSON value (cases: object, array, string, number, boolean, `null`); each other line, including one cut short and a JSON array, adds one to `invalidLines`; empty lines are neither. Red: function missing.
- [ ] T006 Test `groupRequests(records)` in `tests/viewer/traces.test.js`: one entry per `requestId` across files; `time` the earliest record time; `sessionId` and `route` from the earliest record; sorted newest first, ties by `requestId`; `legs` maps each leg with exactly one record to it; `duplicateLegs` lists legs with more than one. Red: function missing.
- [ ] T007 Test `comparisons(request)` in `tests/viewer/traces.test.js`: returns Sent (`client_in`, `upstream_out`) then Received (`upstream_in`, `client_out`); each side `recorded` with its record, `not recorded`, or `duplicate`; each comparison's `shownAs` is `diff` when both sides are recorded, `single` when one is recorded and the other not recorded, `none` when neither is recorded, `duplicate` when either side is duplicate; a recorded side carries `complete`. Red: function missing.
- [ ] T008 Test `payloadOf(record)` in `tests/viewer/traces.test.js`: returns the payload parsed from `record.line`, whatever JSON value it is (cases: object, array, string, number, boolean, `null`). Red: function missing.
- [ ] T009 Implement `traceFiles`, `parseTraceFile`, `groupRequests`, `comparisons` and `payloadOf` in `viewer/traces.js` as an ES module with no DOM access; records keep the raw line, not the parsed payload (research R6). T003–T008 green.

### Phase 3: User Story 1 - See what the proxy changed in a request (Priority: P1) 🎯 MVP

**Goal**: list traced requests and show the Sent and Received comparisons for the selected one.

**Independent Test**: quickstart.md § Check by hand, on a directory with each case.

- [ ] T010 [US1] Test against the proxy's own writer in `tests/integration/test_viewer.py` (contracts/trace-input.md): write, with `FileTap.open(tmp_path, ...)` and `TraceRecord`, a request with all four legs and a session id that `FileTap` names its file by hashing (one with a space), one with only `upstream_out` and `upstream_in`, one with only `client_in` (status 400, payload the text `{`) and no session id, and one whose `upstream_in` and `client_out` have `complete` false; append a line `not json` to one file; add `sub/nested.jsonl` holding a valid record of a fifth request, and `notes.txt`; run `node tests/viewer/summarize.js <dir>` and assert its JSON: the four requests newest first and not the fifth, with their legs, sessions as the records hold them (the spaced id, not its hash; `null` for the `_no-session.jsonl` one), each comparison's `shownAs`, the incomplete flags, and one invalid line reported for that file under its name, without the directory. Red: `summarize.js` missing.
- [ ] T011 [US1] Implement `tests/viewer/summarize.js`: list the directory given as its argument recursively, as paths prefixed with the directory's name (the form a directory input gives), keep those `traceFiles` returns, run `parseTraceFile` on each with its `name`, `groupRequests` and `comparisons` from `viewer/traces.js`, print requests, comparisons and file reports as JSON. T010 green.
- [ ] T012 [US1] Test in `tests/integration/test_viewer.py` that the `.js` files under `viewer/` outside `vendor/` are exactly `viewer/traces.js`, `viewer/tree.js` and `viewer/app.js`, and that none contains `innerHTML`, `outerHTML`, `insertAdjacentHTML` or `document.write` (FR-010). Red: `tree.js` and `app.js` missing; green after T015.
- [ ] T013 [P] [US1] Implement the one-sided tree in `viewer/tree.js`: `renderTree(value)` returns a DOM node for any JSON value; objects and arrays as `<details>` whose children are built on first open; keys and values written with `textContent` only; a number, boolean or `null`, at the root or inside, shown as its JSON text; a string at the root shown as its text (research R2).
- [ ] T014 [US1] Implement `viewer/index.html`: the CSP meta exactly as research R5; a directory input (`webkitdirectory`); areas for the file report, the request list and the comparisons; loads `app.js` as a module and nothing else.
- [ ] T015 [US1] Implement `viewer/app.js`. Handle each `change` on the input as the pick sequence in research R4, steps 1–5 in that order: select with `traceFiles` on `webkitRelativePath` (FR-001), carry each file's `name` into `parseTraceFile` and the file report, report a failed read by the error's `name`. Show the file report when any file has invalid lines or failed to read (FR-007), the empty state when there are no records, and the request list with time, session or "no session", route and each recorded leg with its status, incomplete legs flagged (FR-002, FR-006). On selecting a request, render each comparison by `shownAs`: `diff` as a `json-diff-viewer` with `setData` of the two payloads, `single` as `renderTree` beside a "not recorded" label, `none` as two labels, `duplicate` as a report naming the leg (FR-003–FR-005, FR-008); flag incomplete sides.
- [ ] T016 [US1] Run quickstart.md § Check by hand in Chromium with the proxy's tracing on, every row, including the Network tab (SC-003) and a day of traces timed (SC-004); fix what fails, with a test first where the failure is in `viewer/traces.js`.

### Phase 4: Polish & Cross-Cutting Concerns

- [ ] T017 [P] README: a "Trace viewer" paragraph after the payload tracing settings: the serve command bound to `127.0.0.1`, picking the directory, the two comparisons, and that the page cannot send data.
- [ ] T018 [P] CLAUDE.md: add a line saying `uv run pytest` now runs Node tests too, so it needs Node 22 or later.
- [ ] T019 Run `uv run pytest` and the lint command in CLAUDE.md; all green.

---

## Dependencies & Execution Order

- T001 then T002; T003 next; T004–T008 (one file, so in sequence, all red before T009), then T009.
- T010 then T011. T012 after T011 (same file). T013 in parallel with T010–T012. T014 then T015,
  after T009 and T013.
- T016 after T015. T017 and T018 any time after T015; T019 last.

## Parallel Example

```text
T013 alongside T010      # different files, no shared dependency
```

## Implementation Strategy

One story, one PR: Setup and Foundational give the tested pure module; US1 adds the pinned writer
test and the page; Polish documents it. The page is not usable before T015, so there is no earlier
increment worth shipping.
