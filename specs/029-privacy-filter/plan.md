# Implementation Plan: OpenAI Privacy Filter replaces spaCy NER and the confidential pass

**Branch**: `29-us1-privacy-filter` | **Date**: 2026-11-27 | **Spec**: [spec.md](spec.md)

## Summary

Privacy Filter runs where spaCy NER ran, behind the same `Detector` protocol, so the pipeline, the vault,
the leaf cache and restoration are unchanged. Presidio keeps its card, IBAN, national ID and IP pattern
recognizers, called directly without an NLP engine. The confidential pass goes, with everything only it used.

## Measurements

A synthetic set of 50 samples, every value invented: 20 code samples without PII (Python, JavaScript, Go,
Rust, Bash, SQL, JSON, YAML, a Dockerfile, a Makefile, log lines, a traceback, a diff, #28's reproduction
script among them), 10 code samples with names or addresses, 5 tool outputs, 9 prose samples with PII and 6
without. 39 names and addresses are scored; usernames and bare city names count as neither a hit nor a miss.
spaCy ran as `build_engine()` configures it, at `min_score` 0.4. Privacy Filter ran at its defaults on `cpu`,
8 threads, on a 16-core machine. Spans were trimmed with `trim_span` before scoring; the never-scrub list was
not applied.

| | spaCy | Privacy Filter |
|---|---|---|
| Names and addresses found | 35 of 39 | 34 of 39 |
| False `PERSON` or `ADDRESS` spans, code without PII | 5 | 0 |
| False spans, tool output | 2 (`09:58`, `0.25.2`) | 0 |
| False spans, prose without PII | 5 (`Claude` among them, which the never-scrub list drops) | 0 |
| False spans, code with PII | 1 (`created_at`) | 0 |
| 20 KB of code | 0.41 s | 232.6 s |
| Peak memory | | 4.3 GB |

spaCy's false spans in code were all `PERSON`: `\n").writerows(docs`, `top.head`, `MAX_BATCH`, `app.main`,
`MJ`. Privacy Filter labelled the usernames in the tool outputs: `jdoe` in `ls -la` output, and `/jdoe` and
`mschulz` in paths, the first with its separator (FR-006). It labelled log timestamps `private_date`, which
FR-001 drops. Missed by Privacy Filter: `Daniel Okafor` in `AUTHOR = "..."`, `3 Chestnut Street`, a lone
`Emily`, `Nguyễn Thị Lan`, `Oluwaseun Adeyemi`. Missed by spaCy: two street addresses outside the US and
UK in a CSV, `Nguyễn Thị Lan`, a lone `Grace`.

The set is small: the recall difference is noise; the false-span difference is not. SC-002 runs the 228 KB
benchmark corpus.

Privacy Filter's time grows linearly with input, about 11.6 s per KB on `cpu`. Its `cpu` path copies each
token's four expert weight matrices (`opf/_model/model.py`, `MLPBlock.forward`); its `cuda` path uses Triton
grouped kernels instead. Privacy Filter's code is not changed (spec).

## Privacy Filter detector (`detection/privacy_filter.py`)

- `load_privacy_filter(path: Path, device: str) -> OPF`: on `cpu`, `torch.set_num_threads(os.process_cpu_count())`
  first (FR-004); then `OPF(model=path, device=device)` and one `redact` call, which loads the weights onto the
  device (FR-005). `main()` calls it before `uvicorn.run`, so a failure exits before the port opens.
- `PrivacyFilterDetector(opf)`: `detect` runs in a worker thread under a lock, one `redact` per text, as
  `PresidioDetector` runs spaCy today: the model is CPU- or GPU-bound and not documented as thread-safe. It maps
  labels through `LABEL_CATEGORIES` (FR-001), drops the rest, and raises if a result's `text` differs from
  the text it was given, since its offsets would then point elsewhere.

## Pattern detector (`detection/presidio.py`)

- `build_recognizers()` returns Presidio's `CreditCardRecognizer` with the Mastercard 2-series pattern,
  `IbanRecognizer`, `UsSsnRecognizer`, `NhsRecognizer` and `IpRecognizer`. `PresidioDetector(recognizers,
  min_score)` calls each one's `analyze(text, entities, nlp_artifacts=None)` and keeps results at or above
  `min_score`. Checked on presidio-analyzer as locked: a card, an IBAN, a US SSN, a UK NHS number and an IP
  are found this way, and a card failing Luhn, an NHS number failing its check digit and a known invalid SSN
  are not.
- `ENTITY_CATEGORIES` loses `PERSON`, `LOCATION`, `STREET_ADDRESS`, `EMAIL_ADDRESS` and `PHONE_NUMBER`.
  `build_engine`, `_NoContextBoost`, the tokenizer infixes, `_UNUSED_SPACY_COMPONENTS` and `NER_CHUNK_CHARS`
  go, with `detection/addresses.py` and `detection/chunking.py`.

## Span trimming (`detection/__init__.py`)

`_EDGE_DELIMITERS` gains `/` and `\` (FR-006).

## Confidential pass removed

`detection/confidential.py`; `Pipeline.confidential`, `_scrub_confidentially` and `_apply_findings` in
`scrub.py`; `Stage` and `DetectedSpan.stage`; `Category.CONTEXT` and its entry in
`operator_lists._NOT_SCRUBBABLE_BY_OPERATOR`; the stage key and `confidential_unlocated` in `report.py`; the
`confidential_*` settings; `scripts/e2e/confidential_recall.py`; `tests/fixtures/context-pii/`. `__main__.py`
creates the Privatemode client only when media conversion is enabled, from `privatemode_url` and
`privatemode_timeout_s`.

## Settings (`config.py`)

| Variable | Default | |
|---|---|---|
| `PII_PROXY_PRIVACY_FILTER_PATH` | `~/.cache/llm-pii-proxy/privacy-filter/original` | checkpoint folder: `config.json` and `*.safetensors` |
| `PII_PROXY_PRIVACY_FILTER_DEVICE` | `cpu` | `cpu` or `cuda` |
| `PII_PROXY_PRIVATEMODE_URL` | `http://127.0.0.1:8080` | media conversion only |
| `PII_PROXY_PRIVATEMODE_TIMEOUT_S` | `30` | |

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

## Governing documents

The requirements and contracts change in this spec PR, so they hold before the story starts:
- 001 `spec.md`: the scope paragraph, User Story 2, FR-012 to FR-015, SC-003's wording, SC-004, SC-005, SC-006,
  the assumptions and dependencies that name them.
- 001 `contracts/placeholder-format.md` § Outbound, `contracts/http-interface.md` § Errors;
  `contracts/confidential-detection.md` deleted, with the links to it in 001 `quickstart.md` and `research.md`
  and its entry in 001 `plan.md`. The code and tests that cite it go in the story (T008).
- 023 `spec.md` FR-004.

The documents that describe the code change with it in the story PR (T014), as 023 did: 001 `data-model.md`
(category sources, `CONTEXT`, a span's stage, edge trimming, the leaf cache, operator lists, the scrub report),
001 `plan.md` and `quickstart.md` (design, settings, setup, validation), 023 `plan.md` (the Privatemode
settings) and `README.md`. 001's `research.md`, `tasks.md`, checklists and critiques record what was decided
and done then, and stay.

## Testing

- Tests that need only some spans use a stub `Detector`. The real model is loaded once per test session for
  `test_privacy_filter_detector.py`, `test_no_leak.py` (SC-001 of 001, 16 KB) and the benchmarks.
- `-m benchmark`: SC-002 (`test_local_recall.py`) and SC-003 (`test_code_set.py`, over
  `tests/fixtures/code-set/`). On `cpu` the 228 KB corpus takes tens of minutes.
- `-m gpu`: SC-004, `test_latency.py`; it fails when no CUDA device is usable. It runs on a GPU machine only.
