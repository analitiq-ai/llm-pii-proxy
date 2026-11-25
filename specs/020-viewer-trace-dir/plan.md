# Implementation Plan: Trace viewer reads the configured trace directory

**Branch**: `20-spec` | **Date**: 2026-11-25 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/020-viewer-trace-dir/spec.md`

## Summary

`python -m llm_pii_proxy.viewer` loads the proxy's `Settings`, checks `trace_dir` with the same code
that guards it for the proxy, makes a fresh access key, prints the viewer's address with the key in
the fragment, and serves the page on loopback. The page reads the key from the fragment and asks the
server for the traces; the server answers with every trace file of `trace_dir` in one JSON response,
only when the key matches. Requests addressed to any other host are refused. The page files move into
the package so the command can serve them; the directory picker goes, and everything the page shows
once it has the records stays as it is.

## Technical Context

**Language/Version**: Python 3.13 for the server; JavaScript (ES modules, no build step) for the page.

**Primary Dependencies**: Starlette and uvicorn, both already proxy dependencies (research R1). No new
dependency.

**Storage**: none. Reads trace files; writes nothing.

**Testing**: pytest with Starlette's `TestClient` for the server (key, host, routes, directory checks);
Node 22 test runner for `traces.js`, as today. The page by hand, per quickstart.md.

**Target Platform**: Chromium or Firefox on the operator's machine, the viewer on a loopback address.

**Project Type**: a second entry point in the existing Python package, beside the proxy; the proxy's
request path is not changed (FR-011).

**Performance Goals**: a day of traces listed in under 5 s (SC-005): one response carrying the files,
parsed in the browser as today.

**Constraints**: loopback only; key and Host checked on every trace request; no request-chosen paths;
CSP `connect-src 'self'`; key and trace content never logged.

**Scale/Scope**: one operator, one machine, up to a day of traces.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

`.specify/memory/constitution.md` is the unfilled template, so there are no constitution gates. The
project rules in CLAUDE.md and README apply instead:

| Rule | How the plan meets it |
|---|---|
| Never log bodies or the mapping | trace content goes only into the `/traces` response; the key travels in a URL fragment and a header, neither of which uvicorn's access log records (research R3) |
| Loopback only; host names rejected | the viewer takes `PII_PROXY_HOST` with its existing loopback-IP validator (research R4) |
| Fail closed | a missing key, wrong key or wrong Host gets no trace content; an unsafe or missing trace directory stops startup |
| Define once | the directory checks are extracted from `FileTap.open` and called by both (research R2); settings are the proxy's `Settings` |
| Tests never call the real API | the viewer tests use temp directories and `TestClient`; no upstream |

Re-checked after Phase 1: no change.

## Project Structure

### Documentation (this feature)

```text
specs/020-viewer-trace-dir/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── viewer-http.md
└── tasks.md
```

### Source Code (repository root)

```text
src/llm_pii_proxy/
├── config.py                  # + viewer_port
├── tracing/file_tap.py        # check_trace_dir() extracted from FileTap.open
└── viewer/
    ├── __init__.py
    ├── __main__.py            # load Settings, check trace_dir, make key, print address, run uvicorn
    ├── app.py                 # create_viewer_app(): Host check, page files, /traces
    └── static/                # moved from viewer/ (git mv); index.html, app.js, traces.js, tree.js, vendor/
        ├── index.html         # picker removed; CSP connect-src 'self'
        └── app.js             # fetch /traces with the fragment's key instead of reading picked files

tests/
├── unit/test_file_tap.py      # check_trace_dir refuses a missing directory without creating it
├── integration/test_viewer_server.py   # contract tests: key, Host, routes, file listing, startup
├── integration/test_viewer.py # paths moved; summarize.js reads the directory as the server lists it
└── viewer/                    # traceFiles tests removed with traceFiles; imports point at static/
```

README's "Trace viewer" section is rewritten for the command.

**Structure Decision**: the page moves from `viewer/` into `src/llm_pii_proxy/viewer/static/`, so it is
package data served by the package's own command and found through `importlib.resources`, not
through a path relative to a checkout. This reverses the #16 decision to keep the page outside the
package, which held only while nothing in the package served it.

## Complexity Tracking

None.
