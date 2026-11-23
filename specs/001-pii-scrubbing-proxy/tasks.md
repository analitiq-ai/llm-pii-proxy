---

description: "Task list for the PII-scrubbing proxy for Claude Code"
---

# Tasks: PII-Scrubbing Proxy for Claude Code

**Input**: Design documents from `specs/001-pii-scrubbing-proxy/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Required. The project rules demand a red test first: write each test task, run it, confirm it fails
for the expected reason, and only then do the implementation task that follows it. Tests never call the real
Anthropic API or Privatemode (CLAUDE.md); real-service checks live in `scripts/e2e/` and are run by hand.

**Organization**: Tasks are grouped by user story. US2 adds a detector to US1's pipeline, so it depends on US1
(see Dependencies).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: the user story the task belongs to (US1, US2)
- Paths are relative to the repository root `claude-pii-proxy/`

## Rules that apply to every task

- Never log or put in an error message any request/response body, value, token or vault content (FR-008,
  FR-009). Error messages name the cause only.
- Any failure while scrubbing rejects the request; nothing is forwarded (FR-007).
- `thinking` and `redacted_thinking` blocks are never modified, in either direction (research R3).
- Run everything with `uv run` (`uv run pytest ...`, `uv run ruff ...`).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: dependencies, the secret detector process and its build, test layout

- [X] T001 Update `pyproject.toml`: remove `fastapi`; add runtime deps `starlette`, `httpx-sse`, `pydantic-settings` (keep `httpx`, `presidio-analyzer`, `uvicorn[standard]`, `en-core-web-lg`); add dev deps `hypothesis`, `faker`; register pytest markers `benchmark` (excluded by default via `addopts = "-m 'not benchmark'"`); run `uv lock` and `uv sync`
- [X] T002 [P] Add `.tools/` to `.gitignore`
- [X] T003 [P] Create the secret detector Go module in `sidecar/gitleaks/` (`main.go`, `go.mod` with `go 1.27` and `github.com/zricethezav/gitleaks/v8 v8.30.1`, `go.sum` from `go mod tidy`) implementing contracts/secret-detector.md exactly: argv[1] is the rules file; load it with viper (`SetConfigType("toml")`, `SetConfigFile`, `ReadInConfig`, `Unmarshal` into `config.ViperConfig`, `Translate`); `detect.NewDetector(cfg)` with `IgnoreGitleaksAllow = true` and `MaxDecodeDepth = 5`; first stdout line `{"ready": true, "gitleaks": "<module version from debug.ReadBuildInfo>", "rules": <n>}`; then one JSON request per stdin line `{"id", "texts"}` → one JSON response per line `{"id", "findings": [[{"rule", "secret", "start_line", "end_line", "start_column", "end_column", "tags"}], ...]}` (one list per text); stdin scanner buffer up to 64 MiB; exit 2 on config error, 3 on malformed request, 0 on stdin EOF
- [X] T004 [P] Write `scripts/build-secret-detector.sh`: for linux-amd64, linux-arm64, darwin-amd64 and darwin-arm64, download `go1.27.4.<os>-<arch>.tar.gz` into `.tools/` and verify its sha256 (values from `https://go.dev/dl/?mode=json`, hard-coded in the script; linux-amd64 is `7226736c2339719cda28fbf3a54d71aa63bcefe376e98cef00034268dab24840`); unpack to `.tools/go`; build with `GOTOOLCHAIN=local GOFLAGS=-mod=readonly go build -trimpath -o .tools/gitleaks-sidecar ./sidecar/gitleaks`; keep GOPATH and GOCACHE under `.tools/`; `set -euo pipefail`; idempotent (skips the download when the verified archive is present)
- [X] T005 [P] Create `src/claude_pii_proxy/detection/gitleaks.toml` with the rules file recorded verbatim in research.md R7 (`[extend] useDefault = true` plus `uri-credentials`, `password-literal`, `password-line`), and make sure it ships as package data
- [X] T006 [P] Create test directories `tests/unit/`, `tests/integration/`, `tests/benchmark/`, `tests/fixtures/`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: types and helpers every story uses

**⚠️ CRITICAL**: no user story work can begin until this phase is complete

- [X] T007 [P] Write `tests/unit/test_config.py`: defaults equal the quickstart.md configuration table (`PII_PROXY_HOST=127.0.0.1`, `PII_PROXY_PORT=8787`, `PII_PROXY_UPSTREAM_URL=https://api.anthropic.com`, `PII_PROXY_MIN_SCORE=0.4`, `PII_PROXY_SESSION_IDLE_TTL_S=43200`, `PII_PROXY_SECRET_DETECTOR_PATH=.tools/gitleaks-sidecar`, `PII_PROXY_LISTS_PATH=~/.config/claude-pii-proxy/lists.toml`, `PII_PROXY_CONFIDENTIAL_ENABLED=false`, `PII_PROXY_CONFIDENTIAL_URL=http://127.0.0.1:8080`, `PII_PROXY_CONFIDENTIAL_MODEL=glm-flash-latest`, `PII_PROXY_CONFIDENTIAL_TIMEOUT_S=30`, `PII_PROXY_CONFIDENTIAL_CHUNK_CHARS=24000`, `PII_PROXY_CONFIDENTIAL_CONCURRENCY=4`); "must be loopback": `127.0.0.1`, `::1` accepted, `0.0.0.0` and `192.168.1.10` rejected; invalid values (negative TTL, port 0, score > 1) raise at construction
- [X] T008 Implement `src/claude_pii_proxy/config.py`: pydantic-settings `Settings` with `env_prefix="PII_PROXY_"` and the fields above, loopback validation on `host`, and the constant `PINNED_GITLEAKS_VERSION = "v8.30.1"`
- [X] T009 [P] Write `tests/unit/test_errors.py`: every row of the contracts/http-interface.md "Proxy-originated errors" table maps to its status, `error.type` and (for 503) `retry-after: 5`; body is exactly `{"type":"error","error":{"type":"<type>","message":"pii-proxy: <cause>"}}`
- [X] T010 Implement `src/claude_pii_proxy/errors.py`: a `ProxyError` base (status, error_type, cause, optional retry_after, `body()`) and one subclass per table row: `BadRequest` (400), `ScrubFailed` (400), `UnsupportedEncoding` (415), `RouteNotFound` (404), `ConfidentialUnavailable` (503, retry-after 5), `ConfidentialInvalid` (502), `SecretDetectorUnavailable` (503), `UpstreamUnavailable` (502)
- [X] T011 [P] Write `tests/unit/test_vault.py` against data-model.md and contracts/placeholder-format.md: token is `PII_<CATEGORY>_<ordinal as 4 digits>` ("ordinal: int, 1–9999 | per category, assigned in first-seen order, never reused"); the 10 000th placeholder in one category raises `ScrubFailed`; same value → same token; "A value has one placeholder. If two detectors disagree on its category, the first assignment wins."; "`by_value` and `by_token` are exact inverses"; append-only (no API removes or changes an entry); lookup by token is case-insensitive (`pii_email_0001` finds `PII_EMAIL_0001`); seeding from always-scrub lists in file order gives the same ordinals in two fresh vaults; the scanning regex `PII_([A-Z]{1,10})_([0-9]{4})` (case-insensitive) matches tokens and not `PII_EMAIL_1`; the Category enum has exactly `PERSON, EMAIL, PHONE, ADDRESS, CARD, IBAN, NATIONALID, IP, SECRET, CONTEXT, LITERAL`
- [X] T012 Implement `src/claude_pii_proxy/vault.py`: `Category` enum, `TOKEN_PATTERN`, `format_token(category, ordinal)`, `Vault` (`by_value`, `by_token`, `next_ordinal`, `assign(value, category) -> token`, `value_for(token) -> str | None`, `seed(always_scrub)`)
- [X] T013 [P] Write `tests/unit/test_spans.py` against data-model.md DetectedSpan: "`0 ≤ start < end ≤ len(text)`" is enforced; edge trimming shrinks a span to exclude leading and trailing whitespace and the delimiters `, ; | " '` and tab, and splits it at line breaks (`1,Margaret Ellsworth,` with a span over `,Margaret Ellsworth,` yields `Margaret Ellsworth`); a span empty after trimming is dropped; "when spans overlap, the longest span wins, then the earliest start"
- [X] T014 Implement `src/claude_pii_proxy/detection/__init__.py`: `DetectedSpan(start, end, category, stage)` with `stage` in `local | confidential`; `Detector` protocol `async detect(texts: Sequence[str]) -> list[list[DetectedSpan]]` (one span list per input text); `trim_span`, `resolve_overlaps`
- [X] T015 [P] Write `tests/unit/test_chunking.py`: every chunk ≤ `max_chars`; splits fall on line boundaries; a single line longer than `max_chars` is split hard at the limit with pieces overlapping by 200 characters; each chunk carries its start offset and `text[start:start+len]` equals the chunk; every character of the input is in at least one chunk; empty input gives no chunks
- [X] T016 Implement `src/claude_pii_proxy/detection/chunking.py`: `chunk(text, max_chars, overlap=200) -> list[Chunk]` (shared by Presidio and the confidential pass)
- [X] T017 [P] Write `tests/unit/test_operator_lists.py` against data-model.md Operator lists: absent file → both lists empty; malformed TOML fails; `always_scrub` keyed by Category name, "`LITERAL` and `CONTEXT` not allowed"; "A value on both lists, an empty string, or an unknown category fails startup"; order preserved
- [X] T018 Implement `src/claude_pii_proxy/operator_lists.py`: `OperatorLists(always_scrub: dict[Category, list[str]], never_scrub: frozenset[str])` and `load(path) -> OperatorLists`
- [X] T019 [P] Write `tests/unit/test_report.py`: header value `EMAIL=2,PERSON=1` (categories sorted by name, zero counts omitted), counts are by `(category, stage)` plus `skipped_binary`; the log line carries the session id and the session's running totals per category; a test asserts neither header nor log line contains any input value
- [X] T020 Implement `src/claude_pii_proxy/report.py`: `ScrubReport`, `SessionTotals`, `header_value()`, `log_line(session_id, totals)`
- [X] T021 Write `tests/conftest.py`: `settings` fixture (test defaults, no real URLs reached), `operator_lists` fixture, a deterministic `RegexDetector` test double implementing `Detector` (emails and `SECRET-[A-Z0-9]+` tokens) so integration tests don't need spaCy or the Go binary, and a `respx` upstream router fixture that records every request it receives

**Checkpoint**: foundation ready

---

## Phase 3: User Story 1 - Work in Claude Code without PII leaving the machine (Priority: P1) 🎯 MVP

**Goal**: local detection (Presidio, gitleaks, operator lists), reversible placeholders, restoration in
streamed and non-streamed responses, fail closed.

**Independent Test**: seed a project with known PII and secrets, run scripted Claude Code tasks through the
proxy, confirm none of the seeded values appear in outbound traffic and on-disk results match a run without
the proxy (spec US1; quickstart V1, V2, V5, V10).

### Fixtures

- [X] T022 [P] [US1] Create `tests/fixtures/pii-project/` (`customers.csv` with 2 rows of fake name, email, phone, IBAN, card, SSN, IP; `.env` with a fake AWS key, GitHub token, `DB_PASSWORD`, `DATABASE_URL=postgres://user:pass@host/db`, `LOG_LEVEL`; `CLAUDE.md` with a fake maintainer name, email and phone) and `tests/fixtures/pii-project.planted.txt` listing every planted value, one per line. Use reserved/fake ranges only (`555` phones, `203.0.113.0/24` IPs, test card numbers)

### Secret detection

- [X] T023 [P] [US1] Write `tests/integration/test_secret_detector.py` (needs `.tools/gitleaks-sidecar`; if absent, the test fails with "run scripts/build-secret-detector.sh", never skips): ready line reports `v8.30.1`; a version mismatch fails startup; no ready line within 5 s fails startup; the 13 samples from research R7 are all found and nothing is found in a real Python file; `# gitleaks:allow` doesn't hide a secret; `GITLEAKS_CONFIG` set in the parent environment has no effect; killing the process makes the in-flight call raise `SecretDetectorUnavailable` and the next call succeeds on a fresh process; a call with no response in 10 s raises `SecretDetectorUnavailable`; a base64-encoded token (`decoded:*` tag) becomes a span over the encoded segment, and if the segment can't be located the call raises `ScrubFailed`; findings map to the right leaf when several leaves are sent in one call
- [X] T024 [US1] Implement `src/claude_pii_proxy/detection/gitleaks.py` per contracts/secret-detector.md: start with an empty environment and cwd `/`, check the ready line against `PINNED_GITLEAKS_VERSION`, serialise calls with an `asyncio.Lock`, send cache-miss leaves as one `texts` list, locate `secret` between `start_line` and `end_line` (0-based), map `decoded:*` findings to the run of encoding characters around their columns, return `SECRET` spans per leaf

### PII detection

- [X] T025 [P] [US1] Write `tests/unit/test_presidio_detector.py`: entity → Category map from research R5 (`PERSON`→PERSON, `LOCATION`→ADDRESS, `EMAIL_ADDRESS`→EMAIL, `PHONE_NUMBER`→PHONE, `CREDIT_CARD`→CARD, `IBAN_CODE`→IBAN, `US_SSN`/`UK_NHS`→NATIONALID, `IP_ADDRESS`→IP); `DATE_TIME`, `NRP`, `URL` never returned; spaCy pipeline enabled components are exactly `tok2vec`, `ner`; results below `PII_PROXY_MIN_SCORE` dropped; a leaf over 100 000 characters is chunked (100 000, overlap 200) and a value inside the overlap is reported once with correct offsets; analysis runs off the event loop thread
- [X] T026 [US1] Implement `src/claude_pii_proxy/detection/presidio.py`: one `AnalyzerEngine` built at startup on `en_core_web_lg` with `tagger`, `parser`, `attribute_ruler`, `lemmatizer` disabled; `detect()` via `asyncio.to_thread`; uses `detection/chunking.py`

### Scrubbing

- [X] T027 [P] [US1] Write `tests/unit/test_scrub_leaf.py` against contracts/placeholder-format.md "Outbound": stage 1 escapes text matching the scanning regex case-insensitively as `LITERAL`; stage 2 replaces every vault value longest first under the boundary rule (`Al` replaced in `Al,` and `(Al)`, not in `Alice` or `Al2O3`; edges where the value is punctuation are unconstrained); stage 3 applies detector spans, dropping spans whose exact text is on `never_scrub`; always-scrub values seeded in the vault are replaced without any detector; a Hypothesis property: for random text containing random vault values, `restore(scrub(text)) == text`
- [X] T028 [US1] Implement the leaf pipeline in `src/claude_pii_proxy/scrub.py`: `scrub_leaves(leaves, vault, detectors, never_scrub) -> list[str]` running the three stages; detection is batched across all cache-miss leaves of a request, one call per detector, and the local detectors run concurrently (`asyncio.gather`); the confidential pass (US2) runs after them, on their output (FR-012)
- [X] T029 [P] [US1] Write `tests/unit/test_walker.py` against contracts/http-interface.md "Which strings are scrubbed": `system` as string and as block list; strings at any depth in `messages`; `thinking` and `redacted_thinking` blocks returned byte-identical; values of keys `type`, `id`, `tool_use_id`, `name`, `signature`, `media_type` and the whole `cache_control` object untouched; `source` objects with `type` `base64`, `url`, `file` untouched and counted `skipped_binary`; `source` of type `text` scrubbed; object keys untouched; an unknown block type is scrubbed; every top-level field other than `system` and `messages` (`tools`, `model`, `metadata`, ...) untouched; unknown fields preserved; key order preserved
- [X] T030 [US1] Implement the request walker in `src/claude_pii_proxy/walker.py`: `collect_leaves(body)` and `apply_leaves(body, scrubbed)`, used identically by `/v1/messages` and `/v1/messages/count_tokens`

### Sessions and leaf cache

- [X] T031 [P] [US1] Write `tests/unit/test_sessions.py` against data-model.md Session and Leaf cache: a session is created on a new id with its vault seeded; a cached leaf returns the identical bytes after the vault has grown; two concurrent requests needing the same uncached leaf call the detectors once and get identical bytes (single-flight); on failure "the pending entry is removed and every awaiting request fails closed with the same error. Nothing is cached", and the next attempt scrubs afresh; a leaf is cached even when another leaf of the same request fails; a session idle longer than `PII_PROXY_SESSION_IDLE_TTL_S` is evicted whole (injected clock), and is "never partly evicted"; the session records whether its first request already held assistant turns
- [X] T032 [US1] Implement `src/claude_pii_proxy/sessions.py`: `SessionStore` (clock injected), `Session(vault, leaf_cache, last_seen, joined_mid_conversation)`, leaf cache keyed by sha256 of the raw leaf with per-key pending futures

### Placeholder notice

- [X] T033 [P] [US1] Write `tests/unit/test_notice.py` against contracts/http-interface.md "Placeholder notice": block list → one text block appended last; string → `"\n\n" + NOTICE` appended; absent → `system` set to the notice; the first `system` block never changes; the notice is identical on every call and is appended after scrubbing (its example token is not escaped)
- [X] T034 [US1] Implement `src/claude_pii_proxy/notice.py` with `NOTICE` as a module constant meeting FR-019. Base it on the wording that worked in research R0b, adapted to the `PII_<CATEGORY>_<NNNN>` format: placeholders such as `PII_EMAIL_0001` replace personal data and secrets; a local proxy restores the real values in everything the model sends back, including tool inputs, before they reach the user or any file; use each placeholder verbatim wherever the value is needed and do not refuse, hedge or ask for the value. The notice gives no advice on extracting parts of values: such tasks are out of scope (spec Assumptions), and a tool extracting a part would send it upstream unscrubbed

### Restoration

- [X] T035 [P] [US1] Write `tests/unit/test_restore.py` against contracts/placeholder-format.md "Inbound" and "Streaming holdback": Hypothesis over every split point of a stream containing tokens, case variants and literal text emits exactly the restored text with no partial token; `PII_EMAIL_0007` (unissued) and `PII_EMAIL_1` (not a token) are left as-is, and only the first is counted `unissued`; the held suffix never exceeds 18 characters; the JSON escape mode inserts `json.dumps(v)[1:-1]` so a value with `"` and `\` keeps `partial_json` valid; flush at block end restores the remainder; non-stream restore rewrites every string in `content[*]` except `thinking`/`redacted_thinking` blocks
- [X] T036 [US1] Implement `src/claude_pii_proxy/restore.py`: `HoldbackRestorer(vault, escape)` with `feed(str) -> str` and `flush() -> str`, plus `restore_message(json_body, vault)`
- [X] T037 [P] [US1] Write `tests/unit/test_sse.py` against contracts/http-interface.md "Responses": event order and names preserved; `ping`, `thinking_delta`, `signature_delta`, `message_*`, `error` and unknown events forwarded immediately and unchanged; `text_delta.text` restored raw and `input_json_delta.partial_json` restored JSON-escaped, one restorer per content block index; at `content_block_stop` any held remainder is emitted as one extra delta of the same kind before the stop event
- [X] T038 [US1] Implement `src/claude_pii_proxy/sse.py`: async generator from `httpx-sse` events to re-serialised SSE bytes

### HTTP app

- [X] T039 [P] [US1] Write `tests/integration/test_routes.py` against contracts/http-interface.md: route table (only the 4 routes; anything else `404 not_found_error` with zero upstream calls); `HEAD /api/hello` and `GET /v1/models` forwarded unchanged; query string such as `?beta=true` forwarded; missing `x-claude-code-session-id` on a content route → 400; non-JSON-object body → 400; `content-encoding` set → 415; headers forwarded verbatim except hop-by-hop, `host`, `content-length` (recomputed) and `accept-encoding` (forced `identity`); non-2xx upstream status and body forwarded unmodified; every content-route response carries `x-pii-proxy-scrubbed`; `count_tokens` body scrubbed and its response forwarded unchanged; upstream unreachable → 502
- [X] T040 [P] [US1] Write `tests/integration/test_no_leak.py` using `tests/fixtures/pii-project/` and the real Presidio and gitleaks detectors: a 3-turn conversation with `tool_result` blocks carrying the fixture files sends none of the planted values upstream (SC-001); the same email maps to the same token in every turn (US1 scenario 4); a response containing tokens reaches the client restored (scenario 1); a `.env` key is a token outbound and the restored `Edit` tool input holds the real key (scenario 3); an always-scrub username inside a `system` path is replaced (FR-018); editing an unrelated line leaves the planted name and phone byte-identical in the restored `Edit` input (scenario 2); with the root logger at DEBUG and output captured from every logger (proxy, uvicorn, httpx) and stderr, no planted value or vault value appears in any log record (FR-009)
- [X] T041 [P] [US1] Write `tests/integration/test_fail_closed.py` (SC-007): injected failures in each stage (Presidio raises, secret detector crashes, vault exhausted, unexpected JSON type in `messages`) each give the error-table status with zero upstream calls; no error body contains request content
- [X] T042 [P] [US1] Write `tests/integration/test_streaming.py`: tokens split across SSE chunks reach the client restored with no partial token (US1 scenario 5); streamed `Write` tool input with a value containing `"` stays valid JSON; thinking and signature deltas pass through byte-identical
- [X] T043 [P] [US1] Write `tests/integration/test_history_stability.py` (research R3): replaying a growing conversation, every leaf sent before is byte-identical in each later request; two concurrent same-session requests (subagents) send identical bytes for their shared `system` and CLAUDE.md leaves; the notice block is byte-identical every time; a forwarded 400 whose body mentions `signature` or `cannot be modified`, for a session created mid-conversation, logs the restart hint with the session id and no content
- [X] T044 [US1] Implement `src/claude_pii_proxy/app.py`: Starlette app with the 4 routes; body checks; walker → sessions/leaf cache → pipeline → notice → forward with httpx (streaming); restore JSON or SSE; `x-pii-proxy-scrubbed` header; one log line per request via `report.py`; restart hint; `ProxyError` → its response; nothing forwarded on any exception
- [X] T045 [US1] Implement `src/claude_pii_proxy/__main__.py`: load `Settings` and operator lists, start the secret detector (fail startup on version mismatch or no ready line in 5 s), build the Presidio engine once, run uvicorn on `host`/`port`; stop the secret detector on shutdown

### Benchmark and end-to-end

- [X] T046 [P] [US1] Write `tests/fixtures/make_corpus.py` and commit its output `tests/fixtures/corpus/local.jsonl`: fixed seed; Faker names (full names and lone given names), emails, phones, street addresses, cards (Luhn-valid), IBANs, US SSNs, UK NHS numbers, IPv4/IPv6 and secrets in the R7 sample formats, embedded in code, CSV, logs and prose; each record holds the text and its labelled spans with category
- [X] T047 [US1] Write `tests/benchmark/test_local_recall.py` (marker `benchmark`): SC-003 recall ≥ 95% for emails, phones, cards, IBANs, IPs and national IDs, ≥ 85% for names, ≥ 70% for postal addresses, ≥ 95% for secrets, on `local.jsonl`; prints a per-category report
- [X] T048 [P] [US1] Write `tests/benchmark/test_latency.py` (marker `benchmark`): SC-004 added time to first streamed byte < 0.5 s for a typical request: 20 KB of new content, Python stdlib source with a `local.jsonl` record (PII and secrets) after every 1600 characters, so both local detectors do real work (local detection only, upstream mocked to respond instantly); also records 20 KB of corpus records alone as the worst case, and the 100 KB typical case against SC-004's "about 2.3 seconds per 100 KB"; prints median and max of 10 runs
- [X] T049 [P] [US1] Create `scripts/e2e/` from the R0 harness: `recorder.py` (mitmproxy reverse-mode addon writing request/response pairs to `$CAPTURE_DIR`), `analyze.py` (planted values found in captured requests by JSON region `system`/`messages`/other, thinking block counts, non-200 responses), `tasks.yaml` (≥ 20 tasks over `tests/fixtures/pii-project/`, each with a pass check fixed in advance, including value-reuse tasks and excluding tasks that need to see a literal value or operate on part of one; the SC-002 check compares every planted value the task did not intend to change), and `run_tasks.sh` (runs each task 3 times direct and 3 times through the proxy, prints both success rates and their ratio)
- [ ] T050 [US1] Run quickstart V2, V5, V6 and V10 by hand and record the results in a new research.md section R12: SC-001 (0 planted values), SC-002 (untouched planted values byte-identical, pass checks met), SC-008 (proxy success rate ≥ 95% of direct), no signature errors

**Checkpoint**: US1 is the MVP: usable on its own with no dependency beyond Anthropic

---

## Phase 4: User Story 2 - Catch context-dependent PII with a confidential second pass (Priority: P2)

**Goal**: an optional Privatemode pass over already-scrubbed, never-before-analysed text.

**Independent Test**: with the pass enabled, context-dependent examples the local pass misses are replaced
before reaching Anthropic, and Privatemode only ever receives already-scrubbed text (spec US2; quickstart V7,
V8).

- [X] T051 [US2] Run spike S3 (needs a Privatemode API key from the user): `response_format: json_schema` on `glm-flash-latest` gives schema-conformant output on 20 sample chunks; record latency per 24 000-character chunk and the Privatemode proxy image digest in research.md R8 and quickstart.md
- [X] T052 [P] [US2] Create `tests/fixtures/context-pii/examples.jsonl` (guarded by `tests/unit/test_context_pii_fixture.py`: the local pass leaves every phrase partly unscrubbed) with ≥ 30 hand-written context-dependent examples (including "the only left-handed cardiologist at St. Mary's in Tralee"), each labelled with the exact phrase that must be replaced
- [ ] T053 [P] [US2] Write `tests/unit/test_confidential.py` (respx, no real Privatemode) against contracts/confidential-detection.md: request body matches the contract (model, system instruction, `json_schema` `pii_findings` with `strict: true`, `temperature: 0`, `frequency_penalty: 0.1`, `max_completion_tokens: 2048`); chunks ≤ `PII_PROXY_CONFIDENTIAL_CHUNK_CHARS` from the shared chunker; at most `PII_PROXY_CONFIDENTIAL_CONCURRENCY` in flight; every exact occurrence of a finding becomes a `CONTEXT` span; a finding not found verbatim is dropped and counted `confidential_unlocated`; a finding overlapping a placeholder token is dropped; non-2xx or timeout → `ConfidentialUnavailable` (503); invalid JSON or schema mismatch → `ConfidentialInvalid` (502); disabled → no connection attempted
- [ ] T054 [US2] Implement `src/claude_pii_proxy/detection/confidential.py` (a `Detector` with `stage="confidential"`)
- [ ] T055 [US2] Wire the pass into `src/claude_pii_proxy/scrub.py` behind `PII_PROXY_CONFIDENTIAL_ENABLED`: it runs on each cache-miss leaf's text after the local stages (FR-012), its spans become placeholders through the same vault path (FR-013), and a leaf is cached only once every chunk covering it succeeded
- [ ] T056 [P] [US2] Write `tests/integration/test_confidential_pass.py` (respx for Anthropic and Privatemode): US2 scenarios 1–4; the Privatemode request never contains any vault value (SC-006); a second turn sends only new leaves to Privatemode; Privatemode down → 503 and zero Anthropic calls (FR-014)
- [ ] T057 [P] [US2] Write `scripts/e2e/confidential_recall.py` (real Privatemode, run by hand): SC-005 ≥ 70% of `tests/fixtures/context-pii/` examples replaced
- [ ] T058 [US2] Run quickstart V7 and V8 and `scripts/e2e/confidential_recall.py` by hand; record results in research.md R12

**Checkpoint**: US1 and US2 both work; US2 switches off cleanly with `PII_PROXY_CONFIDENTIAL_ENABLED=false`

---

## Phase 5: Polish & Cross-Cutting Concerns

- [X] T059 [P] Update `README.md`: placeholder example `PII_EMAIL_0001`, setup (`uv sync`, `scripts/build-secret-detector.sh`, operator lists file), run, link to quickstart
- [X] T060 [P] Update the project `CLAUDE.md`: the secret detector build step, `uv run pytest` (default) vs `uv run pytest -m benchmark`, and that `scripts/e2e/` is manual and calls real services
- [X] T061 Run `uv run ruff check --fix` and `uv run ruff format` over `src/` and `tests/`; fix all findings
- [ ] T062 Run the full validation V1–V10 in `specs/001-pii-scrubbing-proxy/quickstart.md` and confirm every result matches its Expected column

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: none
- **Foundational (Phase 2)**: needs T001. Blocks all stories
- **US1 (Phase 3)**: needs Phase 2. T023 also needs T003–T005 built
- **US2 (Phase 4)**: needs US1 complete. It adds a detector to US1's pipeline and is not usable without it
- **Polish (Phase 5)**: needs the stories you are shipping

### Within US1

- Each test task precedes the implementation task after it, and must fail first
- T022 (fixtures) before T040
- T024, T026 before T028; T028 before T030; T030, T032, T034 before T044; T036 before T038; T038 before T044
- T044 before T045; T045 before T048–T050

### Parallel Opportunities

- Phase 1: T002–T006 together after T001
- Phase 2: all test tasks (T007, T009, T011, T013, T015, T017, T019) together; each implementation follows its own test
- US1: the test tasks for different modules (T023, T025, T027, T029, T031, T033, T035, T037) together; integration tests T039–T043 together; T046, T048, T049 together
- US2: T052, T053 together; T056, T057 together

---

## Parallel Example: User Story 1

```bash
# Unit tests for independent US1 modules, written together and all failing:
Task: "Write tests/unit/test_presidio_detector.py"
Task: "Write tests/unit/test_scrub_leaf.py"
Task: "Write tests/unit/test_sessions.py"
Task: "Write tests/unit/test_restore.py"
Task: "Write tests/unit/test_sse.py"

# Integration tests once the modules exist, before app.py:
Task: "Write tests/integration/test_routes.py"
Task: "Write tests/integration/test_no_leak.py"
Task: "Write tests/integration/test_fail_closed.py"
Task: "Write tests/integration/test_streaming.py"
Task: "Write tests/integration/test_history_stability.py"
```

---

## Implementation Strategy

### MVP First (US1 only)

1. Phase 1 and Phase 2
2. Phase 3 (US1)
3. **Stop and validate**: T050 (quickstart V2, V5, V6, V10). SC-008 at 95% is the go/no-go for daily use
4. Use it; US2 waits for a Privatemode key and spike S3

### Incremental Delivery

1. Setup + Foundational → foundation ready
2. US1 → validate → MVP
3. US2 → validate V7/V8 → enable when wanted

---

## Notes

- [P] = different files, no dependency on an incomplete task
- Commit after each test+implementation pair
- A test that passes before its implementation exists is not testing anything: fix the test
