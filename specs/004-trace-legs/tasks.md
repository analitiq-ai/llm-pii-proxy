# Tasks: Payload traces per leg

**Input**: Design documents from `specs/004-trace-legs/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Required. Write each test task, run it, and confirm it fails for the stated reason before the task
that makes it pass.

**Organization**: by user story. US2 (#9) builds on US1 (#8) and is blocked by it.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: US1, US2

## Rules that apply to every task

- Upstream is mocked with respx; no test calls the real API (CLAUDE.md).
- No body or payload reaches the log; failures log leg and exception type only (FR-013, FR-016).
- Tests that create trace directories use `tmp_path`, which is outside any git tree.

---

## US1 block — issue #8

### Phase 1: Setup (Shared Infrastructure)

- [ ] T001 Create the package `src/llm_pii_proxy/tracing/` with an empty `__init__.py`

### Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the record type, the `Tap` interface and the stream assembler that every leg uses.

- [ ] T002 [P] Test `TraceRecord` serialization in `tests/unit/test_trace_record.py`: keys in the order `time, session_id, request_id, leg, route, status, complete, payload`; `time` ISO 8601 UTC with milliseconds and `Z`; one line ending `\n`; non-ASCII kept; `status` may be `null` (contracts/trace-record.md). Red: module missing.
- [ ] T003 [P] Test `MessageAssembler` in `tests/unit/test_assemble.py`, feeding `ServerSentEvent`s: text, thinking and signature deltas appended; `input_json_delta` parsed into `input` at `content_block_stop`; `message_delta` merges `stop_reason`, `stop_sequence`, `usage`; unknown events ignored; a tool block never stopped keeps the accumulated string in `input`; result matches a non-streamed response for the same content (research R3). Red: module missing.
- [ ] T004 [P] Test `RequestTrace` in `tests/unit/test_trace_record.py` with a recording fake tap: `wants(leg)` false for a disabled leg; `record` passes a disabled leg's record to nothing; all records of one request share `request_id` (a `uuid4().hex`, research R10), `session_id` and `route`; `complete` defaults to true. Red: class missing.
- [ ] T005 Implement `Leg` (`upstream_out`, `upstream_in`), `TraceRecord` (frozen dataclass, fields per data-model.md; `session_id: str | None`, `status: int | None`, `complete: bool`), its line encoding, the `Tap` protocol (`record(record) -> None`, never raises), `NullTap`, and `RequestTrace` in `src/llm_pii_proxy/tracing/record.py`. T002 and T004 green.
- [ ] T006 Implement `MessageAssembler` in `src/llm_pii_proxy/tracing/assemble.py` (pure, no I/O). T003 green.
- [ ] T007 Change `restore_stream` in `src/llm_pii_proxy/sse.py` to yield `ServerSentEvent`s and make the encoder public (`encode`); `app.py` encodes. Update `tests/unit/test_sse.py` to assert on events, then run the full suite: no other behaviour changes (research R2).

**Checkpoint**: record type, tap interface and assembler exist; nothing is wired yet.

### Phase 3: User Story 1 — See exactly what Anthropic received and returned (Priority: P1) 🎯 MVP

**Goal**: record `upstream_out` and `upstream_in` to per-session JSONL files, off by default.

**Independent Test**: enable both legs, send a request with a planted email against mocked Anthropic, read the
session file: two records with one request id, holding the placeholder and not the email.

#### Settings

- [ ] T008 [P] [US1] Test in `tests/unit/test_config.py`: `PII_PROXY_TRACE_LEGS` defaults to empty; `upstream_out,upstream_in` parses (comma-separated, whitespace tolerated); an unknown name such as `client_in` fails validation in US1; `PII_PROXY_TRACE_DIR` defaults to `~/.local/state/llm-pii-proxy/traces`, expanded and absolute; `PII_PROXY_TRACE_TTL_H` defaults to 24 and must be > 0 (contracts/configuration.md). Red: fields missing.
- [ ] T009 [US1] Add `trace_legs: frozenset[Leg]` (comma-separated, `NoDecode`), `trace_dir: Path`, `trace_ttl_h: float` to `src/llm_pii_proxy/config.py`. T008 green.

#### File output

- [ ] T010 [P] [US1] Test directory checks in `tests/unit/test_file_tap.py` (research R6): a missing directory is created with mode 0700; a directory inside a tree with a `.git` entry (file or directory, at any parent) fails; an existing directory with mode 0755 or 0500 fails; an owner other than the given uid fails; a `*.jsonl` with mode 0644 fails; each error names the path and its actual mode or owner (FR-009, FR-010). Red: module missing.
- [ ] T011 [P] [US1] Test writing in `tests/unit/test_file_tap.py`: a record lands as one line in `<session id>.jsonl`, created 0600; a session id not matching `[A-Za-z0-9-]{1,128}` is written to `<sha256 hex>.jsonl` (research R5); a symlink at the file path is not followed; when the write fails (the file path is a directory) `record` does not raise and logs the leg and exception type with no payload text (FR-013). Red: class missing.
- [ ] T012 [P] [US1] Test retention in `tests/unit/test_file_tap.py`: `sweep(now)` deletes `*.jsonl` with mtime older than the TTL and keeps newer ones and non-`.jsonl` files (FR-011, research R7). Red: method missing.
- [ ] T013 [US1] Implement `FileTap` in `src/llm_pii_proxy/tracing/file_tap.py`: `check_trace_dir(path, uid)`, `record` (`os.open` with `O_WRONLY|O_APPEND|O_CREAT|O_NOFOLLOW`, mode 0600, one write per record), `sweep(now)`. T010–T012 green.

#### Startup

- [ ] T014 [US1] Test in `tests/integration/test_main.py`: with legs enabled, `build_app` fails before returning when the trace directory is inside a git tree or has the wrong mode; it logs one warning naming the enabled legs and the directory (FR-012); it sweeps expired files; with no legs it creates no directory (FR-001). Red: not wired.
- [ ] T015 [US1] In `src/llm_pii_proxy/__main__.py` `build_app`: choose `NullTap` when no leg is enabled, else check the directory, build `FileTap`, sweep, log the warning; start an hourly sweep task in the lifespan and cancel it at shutdown. Pass the tap and legs to `create_app`. T014 green.

#### Recording

- [ ] T016 [US1] Test in `tests/integration/test_tracing.py`: tracing off writes nothing to the trace directory (AS1, SC-001); non-streamed `/v1/messages` gives one `upstream_out` holding the forwarded body and one `upstream_in` holding the response body, same request id, placeholder present and email absent (AS2); streamed response gives an `upstream_in` with the assembled message written after the stream ends (AS3); only `upstream_out` enabled writes only that record (AS4); `/v1/messages/count_tokens` is traced and `/v1/models` is not; an upstream 4xx is recorded in `upstream_in`; upstream unreachable records `upstream_out` with `status` null (research R8). Red: not wired.
- [ ] T017 [US1] Test in `tests/integration/test_tracing.py`: a stream cut by the client disconnecting writes `upstream_in` with `complete: false` (FR-007); a tap whose writes fail leaves every request succeeding with unchanged responses (FR-013, SC-006); forwarded and returned bytes are identical with tracing on and off for the same input, streamed and not (SC-004). Red: not wired.
- [ ] T018 [US1] Wire `RequestTrace` into `src/llm_pii_proxy/app.py`: one per request on `/v1/messages` and `/v1/messages/count_tokens`; `upstream_out` when the upstream status is known; `upstream_in` after the body is read, or from a `MessageAssembler` fed the upstream events, written in the stream's `finally` with `complete` false when it did not end normally; skip all of it when `wants(leg)` is false. T016 and T017 green.

#### Docs and benchmark

- [ ] T019 [P] [US1] Add a tracing-on case to `tests/benchmark/test_latency.py`: median added latency with both US1 legs on, 100 KB request, under 5 ms (SC-005). Run with `uv run pytest -m benchmark`.
- [ ] T020 [P] [US1] README: Features bullet on payload tracing (the sentence reverted from the spec PR, with the real setting names); add the `PII_PROXY_TRACE_*` settings where README § Configuration points (contracts/configuration.md).

**Checkpoint**: US1 is usable alone: scrubbed legs traced, nothing changes with tracing off.

---

## US2 block — issue #9 (blocked by #8)

### Phase 4: User Story 2 — Compare what came in with what went out (Priority: P2)

**Goal**: record `client_in` and `client_out` behind a second opt-in.

**Independent Test**: enable all four legs with the raw opt-in, send a request with a planted email, check the
four records under one request id; select a raw leg without the opt-in and check startup fails.

- [ ] T021 [P] [US2] Test in `tests/unit/test_config.py`: `client_in` and `client_out` are accepted; selecting either without `PII_PROXY_TRACE_RAW=true` fails validation with an error naming the raw legs and the opt-in (AS1, FR-003, SC-003). Red: legs unknown.
- [ ] T022 [US2] Add `client_in`, `client_out` and the `raw` property to `Leg` in `src/llm_pii_proxy/tracing/record.py`; add `trace_raw: bool = False` and the model validator to `src/llm_pii_proxy/config.py`. T021 green.
- [ ] T023 [P] [US2] Test in `tests/unit/test_file_tap.py`: a record with `session_id` None goes to `_no-session.jsonl` (FR-008). Red: not handled.
- [ ] T024 [US2] Handle `session_id` None in `FileTap` in `src/llm_pii_proxy/tracing/file_tap.py`. T023 green.
- [ ] T025 [US2] Test in `tests/integration/test_tracing.py`: all four legs give four records in the order `client_in`, `upstream_out`, `upstream_in`, `client_out` with one request id; `client_in` holds the email and `upstream_out` its placeholder; streamed `client_out` is the assembled restored message (AS2); a request rejected before forwarding (invalid JSON, missing session header) writes only `client_in` with the rejection status, the latter to `_no-session.jsonl` (AS3, FR-014); a non-JSON body is recorded as text; a cut stream marks `client_out` incomplete (FR-007). Red: not wired.
- [ ] T026 [US2] In `src/llm_pii_proxy/app.py`: record `client_in` (the raw body) once the status is known, including for a `ProxyError` raised before forwarding (status from the error) and any other exception (500), then re-raise; record `client_out` after the restored body, or from a second `MessageAssembler` fed the restored events. T025 green.
- [ ] T027 [US2] Test then implement the warning wording in `tests/integration/test_main.py` and `src/llm_pii_proxy/__main__.py`: with a raw leg enabled, the startup warning says real values are written to disk (AS4, FR-012).

### Phase 5: Polish & Cross-Cutting Concerns

- [ ] T028 [P] Extend the SC-005 benchmark case in `tests/benchmark/test_latency.py` to all four legs.
- [ ] T029 [P] README: describe the raw legs and `PII_PROXY_TRACE_RAW` in the Features bullet and configuration docs.
- [ ] T030 Run `specs/004-trace-legs/quickstart.md` manually with all four legs and a throwaway directory; delete the directory afterwards.

---

## Dependencies & Execution Order

- T001 → T002–T004 (parallel) → T005, T006 → T007.
- US1: T008 → T009; T010–T012 (parallel) → T013; T014 → T015 (needs T009, T013); T016, T017 → T018 (needs T007, T015); T019, T020 after T018.
- US2 starts after #8 merges: T021 → T022; T023 → T024; T025 → T026 (needs T022, T024); T027 after T022.
- Polish after T026.

## Parallel Examples

- Foundational: T002, T003, T004 together; then T005 and T006 together.
- US1: T008, T010, T011, T012 together; T019 and T020 together.
- US2: T021 and T023 together.

## Implementation Strategy

US1 is the MVP: one PR on `8-us1-scrubbed-legs` that closes #8, with Setup and Foundational. It answers "did
anything leak" without writing real values to disk. US2 follows as a second PR on `9-us2-raw-legs`, closing #9.
