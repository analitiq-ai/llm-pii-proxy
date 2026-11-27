# Tasks: OpenAI Privacy Filter replaces spaCy NER and the confidential pass

**Input**: [spec.md](spec.md), [plan.md](plan.md)

**Tests**: Required. Write each test task, run it, and confirm it fails for the stated reason before the
task that makes it pass. Upstream and Privatemode are mocked with `respx`; tests never call real services.

## User Story 1 - Code reaches Anthropic as written, PII is still replaced (#29)

### Setup

- [ ] T001 [US1] In `pyproject.toml` and `uv.lock`, add `opf` as a uv git source at the pinned commit and
  remove `en-core-web-lg`. Add `scripts/fetch-privacy-filter.sh` (plan.md § Dependencies and model setup),
  and a session-scoped `privacy_filter` fixture in `tests/conftest.py` that loads the checkpoint the script
  fetched and fails, never skips, when it is missing.

### Detection

- [ ] T002 [US1] Add `tests/unit/test_privacy_filter_detector.py`, asserting with the real model that a name,
  an email, a phone number and an address come back as `PERSON`, `EMAIL`, `PHONE` and `ADDRESS` at their
  offsets; that a log timestamp and a URL come back as no span (FR-001); that a result whose `text` differs
  from its input raises; and that a `cpu` load sets torch's thread count to the process's CPU count (FR-004).
  Red: the module does not exist.
- [ ] T003 [US1] Write `src/llm_pii_proxy/detection/privacy_filter.py` (plan.md § Privacy Filter detector).
- [ ] T004 [US1] Rewrite `tests/unit/test_presidio_detector.py` for the pattern detector, asserting that a
  card, an IBAN, a US SSN, a UK NHS number and an IP are found, and that a card failing Luhn, an NHS number
  failing its check digit, an email, a phone number and a name are not (FR-002). Red: names, emails and phone
  numbers are found.
- [ ] T005 [US1] Reduce `detection/presidio.py` to plan.md § Pattern detector; delete `detection/addresses.py`
  and `detection/chunking.py` with their tests.
- [ ] T006 [US1] Add to `tests/unit/test_spans.py`: a span with `/` or `\` at either edge is trimmed to the
  value, and a `/` inside a span stays (FR-006, acceptance scenario 7). Red: the separators are kept.
- [ ] T007 [US1] Add `/` and `\` to `_EDGE_DELIMITERS`.

### Confidential pass removed

- [ ] T008 [US1] Delete `detection/confidential.py`, `tests/unit/test_confidential.py`,
  `tests/unit/test_confidential_recall.py`, `tests/integration/test_confidential_pass.py`,
  `tests/unit/test_context_pii_fixture.py`, `tests/fixtures/context-pii/` and
  `scripts/e2e/confidential_recall.py`. Remove `Pipeline.confidential`, `_scrub_confidentially`,
  `_apply_findings`, `Stage`, `DetectedSpan.stage`, `Category.CONTEXT`, the report's stage key and
  `confidential_unlocated`, and update the tests that build spans, reports and operator lists with them.

### Settings and startup

- [ ] T009 [US1] Rewrite `tests/unit/test_config.py` to assert the four settings of plan.md § Settings with
  their defaults, that a device other than `cpu` or `cuda` is refused, and that no `confidential_*` setting
  exists. Add to `tests/integration/test_main.py`: a missing checkpoint, and `cuda` with no usable CUDA
  device, exit before the port opens with the cause named (acceptance scenario 5); media conversion reaches
  Privatemode at `PII_PROXY_PRIVATEMODE_URL`. Red: the settings do not exist.
- [ ] T010 [US1] Change `config.py` and `__main__.py` as plan.md § Settings and § Confidential pass removed
  say; `main()` calls `load_privacy_filter` before `uvicorn.run`.

### Success criteria

- [ ] T011 [US1] Switch `tests/integration/test_no_leak.py` to the real detectors. Add
  `tests/integration/test_code_as_written.py`: #28's reproduction script reaches upstream byte-identical
  (SC-001, acceptance scenario 1), and a Privacy Filter failure blocks the request with nothing forwarded
  (acceptance scenario 6).
- [ ] T012 [US1] Add `tests/fixtures/code-set/` (the 20 code samples of plan.md § Measurements) and
  `tests/benchmark/test_code_set.py` (SC-003); switch `tests/benchmark/test_local_recall.py` to the real
  detectors (SC-002). Run `uv run pytest -m benchmark` and record its results in the PR.
- [ ] T013 [US1] Register a `gpu` marker in `pyproject.toml` and move `tests/benchmark/test_latency.py` to it
  from `benchmark`, asserting 001 SC-004 on `cuda` (SC-004).

### Documentation

- [ ] T014 [US1] Update the documents that describe the code (plan.md § Governing documents). `README.md`
  setup steps run top to bottom on a clean checkout: sync, build the secret detector, fetch Privacy Filter,
  start the proxy.
