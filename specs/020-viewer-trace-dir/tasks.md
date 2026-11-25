# Tasks: Trace viewer reads the configured trace directory

**Input**: Design documents from `specs/020-viewer-trace-dir/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Required. Write each test task, run it, and confirm it fails for the stated reason before the task
that makes it pass.

**Organization**: one user story, US1 (#20).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: US1

## Rules that apply to every task

- No request path is joined to a directory (research R5). No key, trace content or payload in any log
  line (FR-012).
- The page keeps writing trace text with `textContent` or the vendored component only (FR-010); the
  existing markup-API guard in `tests/integration/test_viewer.py` keeps running on the moved files.
- Tests that write trace directories use `tmp_path`, which is outside any git tree, chmod 0700.

---

## US1 block — issue #20

### Phase 1: Setup (Shared Infrastructure)

- [ ] T001 [US1] `git mv viewer src/llm_pii_proxy/viewer/static` and add `src/llm_pii_proxy/viewer/__init__.py`; point `ROOT`-relative paths in `tests/integration/test_viewer.py` and the import in `tests/viewer/summarize.js` and `tests/viewer/traces.test.js` at the new location. `uv run pytest` green: a move, no behaviour change.

### Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the one definition of a safe trace directory (research R2) and the viewer's port setting.

- [ ] T002 [US1] Test in `tests/unit/test_file_tap.py` that `check_trace_dir(path, uid)` raises `TraceDirError` naming the directory for a missing directory and does not create it; for a directory inside a git tree, mode 0755, or another owner (via `uid` argument); for a `*.jsonl` that is mode 0644 or a symlink; and returns for a 0700 directory of 0600 files. The existing `FileTap.open` tests stay as they are. Red: `check_trace_dir` missing.
- [ ] T003 [US1] Extract `check_trace_dir` from `FileTap.open` in `src/llm_pii_proxy/tracing/file_tap.py`; `FileTap.open` keeps refusing a git tree before it creates a missing directory, then calls `check_trace_dir`. T002 and the existing tests green.
- [ ] T004 [P] [US1] Test in `tests/unit/test_config.py` that `Settings().viewer_port` defaults to 8765, reads `PII_PROXY_VIEWER_PORT`, and rejects 0 and 65536. Red: field missing.
- [ ] T005 [US1] Add `viewer_port: int = Field(8765, ge=1, le=65535)` to `src/llm_pii_proxy/config.py`. T004 green.

### Phase 3: User Story 1 - Open the viewer on the proxy's traces (Priority: P1) 🎯 MVP

**Goal**: one command serves the page on the configured trace directory, behind the key and Host checks.

**Independent Test**: quickstart.md, every step.

- [ ] T006 [US1] Contract tests in `tests/integration/test_viewer_server.py` against `create_viewer_app(trace_dir, key, host, port)` with Starlette's `TestClient` (contracts/viewer-http.md, data-model.md):
  - page files: `GET /` returns `index.html` as `text/html; charset=utf-8`; each file under `static/` at its relative path with its content type; `/../pyproject.toml`, `/%2e%2e/pyproject.toml`, `/vendor` and `/nope.js` 404; a file added to `static/` after the app is built 404.
  - key: `GET /traces` without `Authorization`, with a wrong key, or with `Basic` returns 401 and an empty body.
  - listing: with the key, files sorted by name, `text` for each 0600 `*.jsonl`; `subdir/x.jsonl` and `notes.txt` absent; a symlinked `*.jsonl` listed with `error` `OSError`, never its target's text; a directory named `d.jsonl` listed with `error` `NotARegularFile`; a FIFO `p.jsonl` (`os.mkfifo`) listed with `error` `NotARegularFile` without the request hanging, status 200; a file whose last line is cut inside a multi-byte character comes back with its earlier lines intact and the cut line replaced, so `parseTraceFile` counts exactly one invalid line; a file removed between listing and open (wrap `os.scandir` so it deletes one entry after listing) is `error` `FileNotFoundError` while the others keep their `text`, status 200.
  - directory gone: `trace_dir` removed after the app is built gives 500 with body `{"error": "FileNotFoundError"}`, not an empty listing.
  - authority: `authority("127.0.0.1", 8765)` is `127.0.0.1:8765`; `("::1", 8765)` and `("0:0:0:0:0:0:0:1", 8765)` are `[::1]:8765`; `("::ffff:127.0.0.1", 8765)` is `[::ffff:7f00:1]:8765`; `("127.0.0.1", 80)` is `127.0.0.1`. An app bound to `0:0:0:0:0:0:0:1` accepts `Host: [::1]:<port>`; one bound to port 80 accepts `Host: 127.0.0.1`.
  - Host: `example.test:8765`, `127.0.0.1:9999`, `localhost:8765` and no Host header return 421 on `/` and `/traces`; `[::1]:<port>` accepted when bound to `::1`.
  - `POST /traces` 405; every response carries `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`.
  - logs: across the `/traces` cases, `caplog` holds no record containing the key or any trace file's content.

  Red: module missing.
- [ ] T007 [US1] Implement `src/llm_pii_proxy/viewer/app.py`: `authority(host, port)` (data-model.md § Authority); `create_viewer_app` builds the fixed page map from `importlib.resources` at call time (research R5), the exact-Host middleware on `authority` (research R4), the headers (research R6), and `/traces` with `hmac.compare_digest` on the bearer key, returning the listing exactly as data-model.md § Trace listing defines it. T006 green.
- [ ] T008 [US1] Startup tests in `tests/integration/test_viewer_server.py` for `src/llm_pii_proxy/viewer/__main__.py`'s `main()` with `uvicorn.run` replaced by a recorder: with a valid environment it prints exactly one line `Trace viewer: http://127.0.0.1:<port>/#key=<key>` to stdout, `http://[::1]:<port>/#key=<key>` with `PII_PROXY_HOST=0:0:0:0:0:0:0:1`, and `http://[::ffff:7f00:1]:<port>/#key=<key>` with `PII_PROXY_HOST=::ffff:127.0.0.1`, where `<key>` is at least 43 URL-safe characters and differs between two calls, and calls `uvicorn.run` on `PII_PROXY_HOST` and `PII_PROXY_VIEWER_PORT`; with a missing trace directory, mode 0755, or `PII_PROXY_HOST=localhost` it exits non-zero naming the cause and does not call `uvicorn.run`; with `PII_PROXY_LISTS_PATH` pointing at a file of invalid TOML and `PII_PROXY_SECRET_DETECTOR_PATH` at a missing file (both of which stop the proxy) it still prints the address and calls `uvicorn.run`; `caplog` holds no record containing the key. Red: module missing.
- [ ] T009 [US1] Implement `src/llm_pii_proxy/viewer/__main__.py`: `Settings()`, `check_trace_dir(settings.trace_dir, os.getuid())`, `secrets.token_urlsafe(32)`, `print` the address built with `authority` (research R3); load nothing else the proxy loads, `uvicorn.run(create_viewer_app(...), host=settings.host, port=settings.viewer_port, log_config=None)` after `logging.basicConfig` as the proxy's entry point does. T008 green.
- [ ] T010 [US1] Rework the writer test in `tests/integration/test_viewer.py`: write the existing cases with `FileTap` (four requests, hashed session id, no-session file, incomplete legs, a `not json` line; drop the nested and `notes.txt` cases, now covered by T006), fetch `/traces` through `create_viewer_app` and `TestClient`, pipe the JSON to `node tests/viewer/summarize.js` on stdin, and assert the same summary as today. Red: `summarize.js` still reads a directory.
- [ ] T011 [US1] Change `tests/viewer/summarize.js` to read the listing JSON from stdin and run `parseTraceFile`, `groupRequests` and `comparisons` on it; remove `traceFiles` from `static/traces.js` and its test from `tests/viewer/traces.test.js` (the server selects trace files now). T010 green.
- [ ] T012 [US1] Test in `tests/integration/test_viewer.py` that `static/index.html` has no `<input`, that its CSP meta is the current one with `connect-src 'self'` in place of `connect-src 'none'`, and that `static/app.js` contains `fetch("traces"` and no `webkitRelativePath`. Extend the existing blocked-name guard over the page's own scripts with the storage APIs `localStorage`, `sessionStorage`, `indexedDB`, `caches` and `document.cookie` (FR-009: the page now holds a key and fetched traces). Red: picker still there.
- [ ] T013 [US1] Change `static/index.html` (drop the picker label; CSP `connect-src 'self'`) and `static/app.js`: on load, read `key` from `location.hash`; none → show "Open the address the viewer printed." and stop; else `fetch("traces", {headers: {Authorization: "Bearer " + key}, cache: "no-store"})`; 401 → "Key refused: the viewer was restarted; open the address it printed."; 500 whose JSON body has a string `error` → "Trace directory unreadable: <error>"; a rejected fetch, any other status, or a body that does not parse → "Viewer request failed: <status or error name>"; 200 → each `error` entry into the file report as unreadable, each `text` through `parseTraceFile`, then the list as today. T012 green.
- [ ] T014 [US1] Run quickstart.md by hand in Chromium, every step, including the Network tab (SC-004) and a day of traces timed (SC-005); fix what fails, test first.

### Phase 4: Polish & Cross-Cutting Concerns

- [ ] T015 [P] README "Trace viewer": replace the `http.server` command and the picking with `uv run python -m llm_pii_proxy.viewer`, the printed address, reloading for new records, the key and Host checks, and `PII_PROXY_VIEWER_PORT` in the settings table.
- [ ] T016 Run `uv run pytest` and the lint command in CLAUDE.md; all green.

---

## Dependencies & Execution Order

- T001 first. T002 → T003; T004 → T005 in parallel with T002–T003.
- T006 → T007 after T003 and T001. T008 → T009 after T005 and T007.
- T010 → T011 after T007. T012 → T013 after T011 (`app.js` stops using `traceFiles`).
- T014 after T009 and T013. T015 after T013; T016 last.

## Parallel Example

```text
T004 alongside T002      # config test and file_tap test, different files
```

## Implementation Strategy

One story, one PR. The server (T006–T009) is usable with `curl` before the page changes; the page change
(T012–T013) completes the story. No earlier increment is worth shipping alone: without the page change
the operator still has the picker.
