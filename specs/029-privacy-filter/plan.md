# Implementation Plan: OpenAI Privacy Filter replaces spaCy NER and the confidential pass

**Branch**: `29-us1-privacy-filter` | **Date**: 2026-11-27 | **Spec**: [spec.md](spec.md)

## Summary

Privacy Filter runs where spaCy NER ran, behind the same `Detector` protocol, so the pipeline, the vault,
the leaf cache and restoration are unchanged. Presidio keeps its card, IBAN, national ID and IP pattern
recognizers, called directly without an NLP engine. The confidential pass goes, with everything only it used.

## Measurements

A synthetic set of 50 samples, every value invented: 20 code samples (Python, JavaScript, Go, Rust, Bash,
SQL, JSON, YAML, a Dockerfile, a Makefile, log lines, a traceback, a diff, #28's reproduction script among
them), 10 code samples with names, emails or addresses, 5 tool outputs, 9 prose samples with PII and 6
without. #28's script holds one username, `jdoe` in a path; the other 19 code samples hold no name, email, phone number or address. 39 names
and addresses are scored; usernames and bare city names count as neither a hit nor a miss. The set holds 6
emails and no phone numbers. spaCy ran as `build_engine()` configures it, at `min_score` 0.4. Privacy Filter
ran at its defaults on `cpu`, 8 threads, on a 16-core machine. Spans were trimmed with `trim_span` before
scoring; the never-scrub list was not applied.

| | spaCy | Privacy Filter |
|---|---|---|
| Names and addresses found | 35 of 39 | 34 of 39 |
| Emails found whole | 4 of 6 | 5 of 6 |
| False `PERSON` or `ADDRESS` spans, the 20 code samples | 5 | 0 |
| False spans, tool output | 2 (`09:58`, `0.25.2`) | 0 |
| False spans, prose without PII | 5 (`Claude` among them, which the never-scrub list drops) | 0 |
| False spans, code with PII | 1 (`created_at`) | 0 |
| 20 KB of code | 0.41 s | 232.6 s |
| Peak memory | | 4.3 GB |

spaCy's false spans in code were all `PERSON`: `\n").writerows(docs` in #28's script, `top.head`,
`MAX_BATCH`, `app.main`, `MJ`. Privacy Filter labelled the usernames in the tool outputs: `jdoe` in `ls -la`
output, and `/jdoe` and `mschulz` in paths, the first with its separator (FR-006). It labelled log timestamps
`private_date`, which FR-001 drops. Missed by Privacy Filter: `Daniel Okafor` in `AUTHOR = "..."`,
`3 Chestnut Street`, a lone `Emily`, `Nguyễn Thị Lan`, `Oluwaseun Adeyemi`. Missed by spaCy: two street
addresses outside the US and UK in a CSV, `Nguyễn Thị Lan`, a lone `Grace`.

Emails: `mgarcia@build-host` occurs twice, in an `scp` line and the `ssh` line after it. spaCy's run, through
Presidio's email pattern, found neither: the domain has no dot. Privacy Filter found the first whole and the
second as `mgarcia@build` (spec Edge Cases).

The set is small: the recall difference is noise; the false-span difference is not. SC-002 runs the 227 KB
benchmark corpus, which also holds the phone numbers.

Privacy Filter's time grows linearly with input, about 11.6 s per KB on `cpu`. Its `cpu` path copies each
token's four expert weight matrices (`opf/_model/model.py`, `MLPBlock.forward`); its `cuda` path uses Triton
grouped kernels instead. Privacy Filter's code is not changed (spec).

## Privacy Filter detector (`detection/privacy_filter.py`)

- `load_privacy_filter(path: Path, device: str) -> OPF`: raises when `device` is `cuda` and
  `torch.cuda.is_available()` is false. On `cpu` it calls `torch.set_num_threads(os.process_cpu_count())`
  (FR-004). Then `OPF(model=path, device=device)` and one `redact` call, which loads the weights onto the
  device (FR-005).
- `PrivacyFilterDetector(opf)`: the constructor reads `opf.get_runtime().label_info` and raises unless its
  span class names, background excluded, are the eight v2 labels FR-001 names (FR-005). `detect` runs in a
  worker thread under a lock, one `redact` per text, as `PresidioDetector` runs spaCy today: the model is CPU-
  or GPU-bound and not documented as thread-safe. It maps the four labels of `LABEL_CATEGORIES`, drops the
  four of `DROPPED_LABELS`, and raises `ScrubFailed` on any other label (FR-001) and when a result's `text`
  differs from the text it was given, since its offsets would then point elsewhere.
- `main()` builds the detector from `load_privacy_filter` before `uvicorn.run`, so every failure above exits
  before the port opens. `build_app` takes the detector in place of the spaCy engine.

## Pattern detector (`detection/presidio.py`)

- `build_recognizers()` returns Presidio's `CreditCardRecognizer` with the Mastercard 2-series pattern,
  `IbanRecognizer`, `UsSsnRecognizer`, `NhsRecognizer` and `IpRecognizer`. `PresidioDetector(recognizers,
  min_score)` calls each one's `analyze(text, entities, nlp_artifacts=None)` in a worker thread and keeps
  results at or above `min_score`. Checked on presidio-analyzer as locked: a card, an IBAN, a US SSN, a UK NHS
  number and an IP are found this way, and a card failing Luhn, an IBAN failing its checksum, an NHS number
  failing its check digit and a known invalid SSN are not.
- `ENTITY_CATEGORIES` loses `PERSON`, `LOCATION`, `STREET_ADDRESS`, `EMAIL_ADDRESS` and `PHONE_NUMBER`.
  `build_engine`, `Analyzer`, `_NoContextBoost`, the tokenizer infixes, `_UNUSED_SPACY_COMPONENTS` and
  `NER_CHUNK_CHARS` go, with `detection/addresses.py` and `detection/chunking.py`.

## Span trimming (`detection/__init__.py`)

`trim_span` trims `/` and `\` as well (FR-006). A `SECRET` span is trimmed of whitespace only: a gitleaks
span is the secret's own text or the encoded run that holds it (`detection/gitleaks.py`), and base64 can end
in `/`.

## Confidential pass removed

`detection/confidential.py`; `Pipeline.confidential`, `_scrub_confidentially` and `_apply_findings` in
`scrub.py`; `Stage` and `DetectedSpan.stage`; `Category.CONTEXT` and its entry in
`operator_lists._NOT_SCRUBBABLE_BY_OPERATOR`; the stage key and `confidential_unlocated` in `report.py`; the
`confidential_*` settings; `scripts/e2e/confidential_recall.py`; `tests/fixtures/context-pii/`. `__main__.py`
creates the Privatemode client only when media conversion is enabled, from `privatemode_url` and
`privatemode_timeout_s`; `scripts/e2e/media.py` is changed to read them in place of `confidential_url` and
`confidential_timeout_s`.

## Settings (`config.py`)

| Variable | Default | |
|---|---|---|
| `PII_PROXY_PRIVACY_FILTER_PATH` | `~/.cache/llm-pii-proxy/privacy-filter/original` | checkpoint folder: `config.json` and `*.safetensors`; `~` expanded and resolved against the startup directory, as `config.py`'s `_absolute` does for the other paths |
| `PII_PROXY_PRIVACY_FILTER_DEVICE` | `cpu` | `cpu` or `cuda` |
| `PII_PROXY_PRIVATEMODE_URL` | `http://127.0.0.1:8080` | media conversion only; a base URL |
| `PII_PROXY_PRIVATEMODE_TIMEOUT_S` | `30` | above 0 |

`PII_PROXY_MIN_SCORE` stays: it now applies to the pattern recognizers only.

## Dependencies and model setup

- `pyproject.toml`: `en-core-web-lg` and its source go. `opf` comes from
  `https://github.com/openai/privacy-filter` at commit `f7f00ca7fb869683eb732c010299d901457f19c3`, as a uv git
  source: it is not on PyPI, and the name `opf` there is unclaimed, so it must never resolve from an index.
  PyTorch comes from PyPI, whose Linux wheels are the CUDA build and serve both devices.
- `scripts/fetch-privacy-filter.sh` runs `hf download openai/privacy-filter --revision
  7ffa9a043d54d1be65afb281eddf0ffbe629385b --include 'original/*' --local-dir <dir>`, then one detection, so
  tiktoken caches the `o200k_base` encoding. tiktoken checks the encoding's hash. Its cache is
  `TIKTOKEN_CACHE_DIR`, else the system temp folder, so after a reboot that clears it the next start downloads
  it again.

## Files the story changes

From `git grep -liE 'confidential|spacy|en[_-]core[_-]web|chunking|addresses|\bStage\b|CONTEXT\b|build_engine|presidio_engine|context-pii' -- ':!specs' ':!.specify' ':!uv.lock'`
at this spec's commit, less the matches that name none of them (`.claude/skills/`, `app.py`'s word "stage",
the corpus's `context` field, `test_fail_closed.py`'s error message):

- Deleted: `detection/addresses.py`, `detection/chunking.py`, `detection/confidential.py`,
  `scripts/e2e/confidential_recall.py`, `tests/fixtures/context-pii/`, `tests/unit/test_chunking.py`,
  `tests/unit/test_confidential.py`, `tests/unit/test_confidential_recall.py`,
  `tests/unit/test_context_pii_fixture.py`, `tests/integration/test_confidential_pass.py`.
- Changed, code: `__main__.py`, `config.py`, `detection/__init__.py`, `detection/gitleaks.py`,
  `detection/presidio.py`, `operator_lists.py`, `report.py`, `scrub.py`, `vault.py`, `scripts/e2e/media.py`,
  `pyproject.toml`, `uv.lock`.
- Changed, tests: `tests/conftest.py`, `tests/benchmark/test_latency.py`, `tests/benchmark/test_local_recall.py`,
  `tests/integration/test_history_stability.py`, `test_main.py`, `test_media.py`, `test_no_leak.py`,
  `tests/unit/test_config.py`, `test_operator_lists.py`, `test_presidio_detector.py`, `test_report.py`,
  `test_scrub_leaf.py`, `test_sessions.py`, `test_spans.py`, `test_vault.py`.
- Changed, documents that say how to run and develop the code (T014): `README.md` § Install and
  § Configuration, `CLAUDE.md`, 001 `plan.md` and `quickstart.md`, 023 `plan.md`.

## Governing documents

The requirements and contracts change in this spec PR, so they hold before the story starts:
- 001 `spec.md`: the scope paragraph, User Story 2, FR-012 to FR-015, SC-003's wording, SC-004, SC-005, SC-006,
  the edge case on false positives, the assumption that a placeholder replaces the whole value, the assumptions
  and dependencies that name them.
- 001 `data-model.md`: the category sources, `CONTEXT`, a span's stage, edge trimming, the leaf cache's
  detectors, the operator lists' categories, the scrub report's key.
- `README.md` § Features and § Limits, which `CLAUDE.md` makes requirements.
- 001 `contracts/placeholder-format.md` § Outbound, `contracts/http-interface.md` § Errors;
  `contracts/confidential-detection.md` deleted, with the links to it in 001 `quickstart.md` and `research.md`
  and its entry in 001 `plan.md`.
- 023 `spec.md` FR-004.

001's `research.md`, `tasks.md`, checklists and critiques record what was decided and done then, and stay.

## Testing

- T012 makes `addopts` deselect `gpu` as well as `benchmark`. Tests that need only some spans use a stub
  `Detector`. The real
  model is loaded once per test session (`privacy_filter` fixture) for the detector tests, `test_main.py`,
  `test_no_leak.py` and the benchmarks.
- Tests written before the code they grade name their red reason in tasks.md. In tests that go through
  `build_app`, the swap of `presidio_engine` for the detector is the one edit after they are red.
- `-m benchmark`: SC-002 (`test_local_recall.py`), SC-003 (`test_code_set.py`, over `tests/fixtures/code-set/`)
  and `test_tracing_adds_under_5ms_at_100kb`. On `cpu` the 227 KB corpus takes tens of minutes.
- `-m gpu` (T012): SC-004, the two 001 SC-004 tests of `test_latency.py`, changed to load Privacy Filter on
  `cuda`, so they fail when no CUDA device is usable. They run on a GPU machine only.
