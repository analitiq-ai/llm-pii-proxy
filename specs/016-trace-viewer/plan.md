# Implementation Plan: Trace viewer

**Branch**: `16-spec` | **Date**: 2026-11-25 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/016-trace-viewer/spec.md`

## Summary

A static page in `viewer/` that reads the trace directory the operator picks, lists the traced
requests, and shows for a selected one the Sent (`client_in` / `upstream_out`) and Received
(`upstream_in` / `client_out`) comparisons. The two-panel diffs are the vendored MIT
`json-diff-viewer-component`; the viewer's own code is the parsing, grouping and pairing (a pure
module), a small tree for one-sided comparisons, and the page. It is served from loopback by Python's
`http.server`; trace data is read in the browser and never crosses the server. A Content-Security-
Policy stops the page from reaching the network.

## Technical Context

**Language/Version**: JavaScript (ES modules, no build step) for the page; Python 3.13 for the test
that pins the viewer to the proxy's writer.

**Primary Dependencies**: `json-diff-viewer-component` 0.6.4, vendored unmodified (research R1). No
npm, no bundler.

**Storage**: none. Reads trace files through the browser; writes nothing (FR-009).

**Testing**: Node 22 built-in test runner for the pure module; pytest runs it and checks the module
against records written by the proxy's `FileTap` (research R7). The page by hand, per quickstart.md.

**Target Platform**: Chromium or Firefox on the operator's machine, page served from `127.0.0.1`.

**Project Type**: a static page beside the existing Python proxy; the proxy is not changed (FR-012).

**Performance Goals**: a day of traces listed in under 5 s (SC-004): each line parsed once, payloads
parsed again only for the selected request (research R6).

**Constraints**: no network access from the page (FR-009, research R5); payload text never
interpreted (FR-010).

**Scale/Scope**: one operator, one machine, up to a day of traces (the default retention).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

`.specify/memory/constitution.md` is the unfilled template, so there are no constitution gates. The
project rules in CLAUDE.md and README apply instead:

| Rule | How the plan meets it |
|---|---|
| Bodies are written only by payload tracing, to trace files, never to the log | the viewer only reads trace files; it logs nothing and writes nothing |
| Real values stay on the machine (README § Features) | CSP `connect-src 'none'`, `default-src 'none'`; loopback-only server serving the page's own files |
| Tests never call the real API | the tests use records written to a temp directory; no proxy traffic |
| Fail loud | invalid lines and duplicate legs are reported, not dropped (FR-007, FR-008) |

Re-checked after Phase 1: no change.

## Project Structure

### Documentation (this feature)

```text
specs/016-trace-viewer/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── trace-input.md
└── tasks.md
```

### Source Code (repository root)

```text
viewer/
├── index.html            # page, CSP meta, directory input, list and comparison areas
├── app.js                # DOM wiring: read the picked files, render list, reports and comparisons
├── traces.js             # pure: pick trace files, parse lines, group by request, derive comparisons
├── tree.js               # read-only collapsible tree for one-sided comparisons (research R2)
└── vendor/
    └── json-diff-viewer/ # json-diff-viewer-component 0.6.4 src/lib files + LICENSE, unmodified

tests/
├── viewer/
│   ├── traces.test.js    # node --test unit tests of traces.js
│   └── summarize.js      # runs traces.js over a directory, prints the result as JSON
└── integration/
    └── test_viewer.py    # runs the Node tests; writes traces with FileTap and checks summarize.js
```

README gains a short "Trace viewer" paragraph under the payload tracing settings; CLAUDE.md notes that
the tests need Node 22.

**Structure Decision**: the page sits at the repo root in `viewer/`, outside the Python package: it
is not imported by the proxy and is not shipped in its wheel. Its tests sit under `tests/` with the
rest.

## Complexity Tracking

None.
