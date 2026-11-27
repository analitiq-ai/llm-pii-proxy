# Tasks: OpenAI Privacy Filter replaces spaCy NER and the confidential pass

**Input**: [spec.md](spec.md), [plan.md](plan.md)

**Tests**: Required. Write each test task, run it, and confirm it fails for the stated reason before the
task that makes it pass. Upstream and Privatemode are mocked with `respx`; tests never call real services.
The file list is plan.md § Files the story changes.

## User Story 1 - Code reaches Anthropic as written, PII is still replaced (#29)

### Setup

- [ ] T001 [US1] In `pyproject.toml` and `uv.lock`, add `opf` as a uv git source at the pinned commit. Add
  `scripts/fetch-privacy-filter.sh` (plan.md § Dependencies and model setup). `en-core-web-lg` stays until
  T009: T002, T003 and T008 are red against spaCy.

### Success criteria, red against spaCy

- [ ] T002 [US1] Add `tests/fixtures/issue-28-script.txt` and `tests/integration/test_code_as_written.py`:
  through `build_app`, with `jdoe` on the always-scrub list, the script reaches upstream byte-identical except
  that `jdoe` is replaced (SC-001, acceptance scenario 1). Red: spaCy labels `\n").writerows(docs` `PERSON`.
- [ ] T003 [US1] Add `tests/fixtures/code-set/` (plan.md § Measurements: the 19 code samples with no name, email, phone number or address) and
  `tests/benchmark/test_code_set.py`: through `build_app`, no `PERSON`, `ADDRESS`, `EMAIL` or `PHONE`
  placeholder reaches upstream (SC-003). Red: spaCy labels `top.head`, `MAX_BATCH`, `app.main` and `MJ`
  `PERSON`.

### Confidential pass removed

- [ ] T004 [US1] In `tests/unit/test_config.py`, assert that `privatemode_url` (a base URL) and
  `privatemode_timeout_s` (above 0) exist with plan.md § Settings defaults, and that no `confidential_*`
  setting exists. In `tests/integration/test_main.py`, delete the confidential-pass test and point the media
  test at `privatemode_url`. Red: the settings do not exist.
- [ ] T005 [US1] Remove the confidential pass (plan.md § Confidential pass removed): delete its files and
  symbols, rename the two settings, and update the tests that build spans, reports and operator lists with
  them, and `tests/conftest.py`, `tests/integration/test_media.py` and `scripts/e2e/media.py`.

### Privacy Filter detector

- [ ] T006 [US1] Add `tests/unit/test_privacy_filter_detector.py`:
  - with the real model, a name, an email, a phone number and an address come back as `PERSON`, `EMAIL`,
    `PHONE` and `ADDRESS` at their offsets, and a log timestamp and a URL as no span;
  - with a fake `OPF`, parametrized over the eight v2 labels, each mapped label gives its category and each
    dropped label no span; an unknown label, and a result whose `text` differs from its input, raise
    `ScrubFailed`, and a `redact` that raises makes `detect` raise (acceptance scenario 6); a label set other
    than v2 fails the constructor; `load_privacy_filter` calls `redact` once before it returns (FR-005);
  - `redact` runs off the event loop thread, and two concurrent `detect` calls never run it at once;
  - after `torch.set_num_threads(1)`, a `cpu` load sets the thread count to `os.process_cpu_count()`
    (FR-004); `cuda` with `torch.cuda.is_available` patched false, a missing checkpoint and a truncated
    `model.safetensors` each raise naming the cause (acceptance scenario 5).

  Red: `detection/privacy_filter.py` does not exist.
- [ ] T007 [US1] Write `detection/privacy_filter.py` (plan.md § Privacy Filter detector), and a session-scoped
  `privacy_filter` fixture in `tests/conftest.py` that loads the checkpoint the fetch script wrote and fails,
  never skips, when it is missing.

### Switch to Privacy Filter

- [ ] T008 [US1] Tests for the switch:
  - Rewrite `tests/unit/test_presidio_detector.py` for the pattern detector. Keep the score threshold, the
    off-event-loop thread, one result list per text, and the 2-series Mastercard with its Luhn-invalid twin.
    Assert that a card, an IBAN, a US SSN, a UK NHS number and an IP are found, and that an IBAN failing its
    checksum, the SSN `078-05-1120`, an NHS number failing its check digit, an email, a phone number and a
    name are not (FR-002). Its tests of single names and addresses go: Privacy Filter's recall is bounded by
    SC-002.
  - In `tests/unit/test_config.py`, assert `privacy_filter_path` (`~` expanded and resolved like the other paths, plan.md § Settings) and
    `privacy_filter_device` (`cpu` or `cuda`, anything else refused) with plan.md § Settings defaults.
  - In `tests/integration/test_main.py`, assert that `main()` with a missing checkpoint, and with `cuda`
    configured and `torch.cuda.is_available` patched false, raises naming the cause and never calls
    `uvicorn.run` (FR-004, FR-005).

  Red: names, emails and phone numbers are found; the settings do not exist; `main()` never loads Privacy
  Filter.
- [ ] T009 [US1] Reduce `detection/presidio.py` to plan.md § Pattern detector and delete
  `detection/addresses.py`, `detection/chunking.py` and `tests/unit/test_chunking.py`. Remove
  `en-core-web-lg` from `pyproject.toml` and `uv.lock`. Add the two settings;
  `build_app` takes the Privacy Filter detector and `main()` loads it first (plan.md § Privacy Filter
  detector). Replace `presidio_engine` with `privacy_filter` in every test and delete it. T002 and T003 pass.

### Span trimming

- [ ] T010 [US1] Add to `tests/unit/test_spans.py`: `/` or `\` at either edge of a `PERSON` span is trimmed,
  and a `/` inside it stays (FR-006, acceptance scenario 7); a `SECRET` span ending in `/`, `,`, `;` or `|`
  keeps it and still loses surrounding whitespace (acceptance scenario 4). Red: the separators are kept, and
  the `SECRET` span loses its `,`, `;` and `|`.
- [ ] T011 [US1] Change `trim_span` as plan.md § Span trimming says.

### Benchmarks

- [ ] T012 [US1] Register a `gpu` marker in `pyproject.toml` and make `addopts` deselect it as well as
  `benchmark`. In `tests/benchmark/test_latency.py`, mark the two 001 SC-004 tests `gpu`, load Privacy Filter
  for them on `cuda`, and mark the tracing test `benchmark` (SC-004). Not run while this story is built: no GPU is available.
- [ ] T013 [US1] Run `uv run pytest -m benchmark` (SC-002, SC-003) and record its results in the PR.

### Documentation

- [ ] T014 [US1] Update the documents that say how to run and develop the code (plan.md § Files the story
  changes). `README.md` setup steps run top to bottom on a clean checkout: sync, build the secret detector,
  fetch Privacy Filter, start the proxy.
  `CLAUDE.md` says what `uv run pytest`, `-m benchmark` and `-m gpu` run.
