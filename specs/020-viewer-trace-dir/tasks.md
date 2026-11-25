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

- [ ] T006 [US1] Contract tests in `tests/integration/test_viewer_server.py` against `authority(host, port)` and `create_viewer_app(trace_dir, key, host, port)` with Starlette's `TestClient`. Each case below grades a rule of contracts/viewer-http.md or data-model.md; the rules are not restated here.
  - authority: `("127.0.0.1", 8765)` → `127.0.0.1:8765`; `("::1", 8765)`, `("0:0:0:0:0:0:0:1", 8765)` and `("::1%lo", 8765)` → `[::1]:8765`; `("::ffff:127.0.0.1", 8765)` → `[::ffff:7f00:1]:8765`; `("127.0.0.1", 80)` → `127.0.0.1`.
  - Host: `example.test:8765`, `127.0.0.1:9999`, `localhost:8765` and no Host header → 421 on `/` and `/traces`; an app bound to `0:0:0:0:0:0:0:1` accepts `[::1]:<port>`; one bound to port 80 accepts `127.0.0.1`.
  - page files: `GET /` is `index.html` as `text/html; charset=utf-8`; each file under `static/` at its relative path with its content type; `/../pyproject.toml`, `/%2e%2e/pyproject.toml`, `/vendor`, `/nope.js` and a file added to `static/` after the app is built → 404.
  - key: no `Authorization`, a wrong key, or `Basic` → 401, empty body.
  - listing members: sorted by name; `subdir/x.jsonl` and `notes.txt` absent.
  - per member: `text` for a 0600 `*.jsonl`; a symlinked `*.jsonl` → `error` `OSError`, never its target's text; a directory `d.jsonl` and a FIFO `p.jsonl` (`os.mkfifo`) → `error` `NotARegularFile`, the request not hanging; a file whose last line is cut inside a multi-byte character keeps its earlier lines and `parseTraceFile` counts exactly one invalid line; a file removed between listing and open (wrap `os.scandir` to delete one entry after listing) → `error` `FileNotFoundError`, the others keep `text`, status 200.
  - whole response: `trace_dir` removed after the app is built → 500 `{"error": "FileNotFoundError"}`; an iterator that raises `PermissionError` after its first entry → 500 `{"error": "PermissionError"}`; `os.scandir` raising `ValueError`, with `raise_server_exceptions=False` → 500 whose body is not `{"error": ...}` JSON.
  - `POST /traces` → 405; every response carries the three headers.
  - logs: across the `/traces` cases, `caplog` holds no record containing the key or any trace file's content.

  Red: module missing.
- [ ] T007 [US1] Implement `src/llm_pii_proxy/viewer/app.py`: `authority` (data-model.md § Authority); `create_viewer_app` with the page map from `importlib.resources` built at call time (research R5), the Host middleware (research R4), the headers (research R6), and `/traces` with `hmac.compare_digest` on the bearer key, returning data-model.md § Trace listing. T006 green.
- [ ] T008 [US1] Startup tests in `tests/integration/test_viewer_server.py` for `src/llm_pii_proxy/viewer/__main__.py`'s `main()`, with `uvicorn.run` replaced by a recorder (contracts/viewer-http.md § Startup):
  - valid environment: exactly one stdout line `Trace viewer: http://<authority>/#key=<key>`, the authority `127.0.0.1:<port>` by default, `[::1]:<port>` with `PII_PROXY_HOST` `0:0:0:0:0:0:0:1` and with `::1%lo`, `[::ffff:7f00:1]:<port>` with `::ffff:127.0.0.1`; `<key>` at least 43 URL-safe characters, different between two calls; `uvicorn.run` called with `PII_PROXY_HOST` and `PII_PROXY_VIEWER_PORT`.
  - refused: a missing trace directory, mode 0755, `PII_PROXY_HOST=localhost` → non-zero exit naming the cause, `uvicorn.run` not called.
  - not the proxy's loaders: `PII_PROXY_LISTS_PATH` at a file of invalid TOML and `PII_PROXY_SECRET_DETECTOR_PATH` at a missing file (both stop the proxy) → the address is still printed and `uvicorn.run` called.
  - logs: `caplog` holds no record containing the key.

  Red: module missing.
- [ ] T009 [US1] Implement `src/llm_pii_proxy/viewer/__main__.py`: `logging.basicConfig` as the proxy's entry point does, `Settings()`, `check_trace_dir(settings.trace_dir, os.getuid())`, `secrets.token_urlsafe(32)`, `print` the address built with `authority` (research R3), `uvicorn.run(create_viewer_app(...), host=settings.host, port=settings.viewer_port, log_config=None)`. Nothing else the proxy loads. T008 green.
- [ ] T010 [US1] Rework the writer test in `tests/integration/test_viewer.py`: write the existing cases with `FileTap` (four requests, hashed session id, no-session file, incomplete legs, a `not json` line; drop the nested and `notes.txt` cases, now in T006), fetch `/traces` through `create_viewer_app` and `TestClient`, pipe the JSON to `node tests/viewer/summarize.js` on stdin, and assert the same summary as today. Red: `summarize.js` still reads a directory.
- [ ] T011 [US1] Change `tests/viewer/summarize.js` to read the listing JSON from stdin and run `parseTraceFile`, `groupRequests` and `comparisons` on it; remove `traceFiles` from `static/traces.js` and its test from `tests/viewer/traces.test.js` (the server selects trace files now). T010 green.
- [ ] T012 [US1] Node tests in `tests/viewer/traces.test.js` for the page-outcome function of data-model.md § Page outcomes, one case per table row, plus: a `text/plain` 500, a 500 whose `error` is a number, a 502, a 200 whose body is not JSON, and a rejected fetch (`TypeError`) all land in the "Viewer request failed" row. Red: function missing.
- [ ] T013 [US1] Implement that function in `static/traces.js`, with no DOM access. T012 green.
- [ ] T014 [US1] Test in `tests/integration/test_viewer.py` that `static/index.html` has no `<input`, that its CSP meta is the current one with `connect-src 'self'` in place of `connect-src 'none'`, and that `static/app.js` contains `fetch("traces"` and no `webkitRelativePath`. Extend the existing blocked-name guard over the page's own scripts with `localStorage`, `sessionStorage`, `indexedDB`, `caches` and `document.cookie` (FR-009: the page now holds a key and fetched traces). Red: picker still there.
- [ ] T015 [US1] Change `static/index.html` (drop the picker label; CSP `connect-src 'self'`) and `static/app.js`: on load, `fetch("traces", {headers: {Authorization: "Bearer " + key}, cache: "no-store"})` with the key from `location.hash`, hand the result (or the rejection) to the T013 function, and render its outcome; the list and comparisons as today. T014 green.
- [ ] T016 [US1] Run quickstart.md by hand in Chromium, every step, including the Network tab (SC-004) and a day of traces timed (SC-005); fix what fails, test first.

### Phase 4: Polish & Cross-Cutting Concerns

- [ ] T017 [P] README "Trace viewer": replace the `http.server` command and the picking with `uv run python -m llm_pii_proxy.viewer`, the printed address, reloading for new records, the key and Host checks, and `PII_PROXY_VIEWER_PORT` in the settings table.
- [ ] T018 Run `uv run pytest` and the lint command in CLAUDE.md; all green.

---

## Dependencies & Execution Order

- T001 first. T002 → T003; T004 → T005 in parallel with T002–T003.
- T006 → T007 after T003 and T001. T008 → T009 after T005 and T007.
- T010 → T011 after T007. T012 → T013 after T011 (same module). T014 → T015 after T013.
- T016 after T009 and T015. T017 after T015; T018 last.

## Parallel Example

```text
T004 alongside T002      # config test and file_tap test, different files
```

## Implementation Strategy

One story, one PR. The server (T006–T009) is usable with `curl` before the page changes; the page change
(T012–T015) completes the story. No earlier increment is worth shipping alone: without the page change
the operator still has the picker.
