# Implementation Plan: Remove the confidential pass

**Branch**: `33-us1-remove-confidential-pass` | **Date**: 2026-11-27 | **Spec**: [spec.md](spec.md)

## Summary

The confidential pass goes, with everything only it uses. Local detection, the vault, the leaf cache,
restoration and media conversion stay as they are; media conversion reads two renamed settings.

## Removed (FR-001)

- `detection/confidential.py`; `Pipeline.confidential`, `_scrub_confidentially` and `_apply_findings` in
  `scrub.py`. `_token_spans`, `_known_spans` and `_new_spans` stay: the local pass uses them.
- `Stage` and `DetectedSpan.stage` in `detection/__init__.py`, and the stage argument wherever a span is built.
- `Category.CONTEXT`, and its entry in `operator_lists._NOT_SCRUBBABLE_BY_OPERATOR`. A `CONTEXT` always-scrub
  entry then fails startup as an unknown category.
- In `report.py`: `ScrubReport.counts` is keyed by category alone, so `add` takes no stage and
  `by_category()` goes, its callers reading `counts`; `confidential_unlocated` goes from the class and the log
  line.
- In `config.py`: `confidential_enabled`, `confidential_model`, `confidential_chunk_chars`,
  `confidential_concurrency`, and the `DEFAULT_OVERLAP` import only `confidential_chunk_chars` used.
- `scripts/e2e/confidential_recall.py` and `tests/fixtures/context-pii/`.

## Settings (FR-002, FR-003)

`confidential_url` becomes `privatemode_url` and `confidential_timeout_s` becomes `privatemode_timeout_s`,
keeping their defaults and validators. `privatemode_timeout_s` also stays the PDF render timeout, as
`confidential_timeout_s` is today (023 plan.md). `build_app` (`__main__.py`) creates the Privatemode client
only when `media_enabled` is true.

## Files the story changes

From `git grep -lE '[Cc]onfidential|CONFIDENTIAL|CONTEXT|\bStage\b|context-pii' -- ':!specs' ':!.specify'
':!.claude' ':!uv.lock'` at this spec's commit, less `tests/fixtures/make_corpus.py`, whose `CONTEXTS` are
document kinds:

- Deleted: `detection/confidential.py`, `scripts/e2e/confidential_recall.py`, `tests/fixtures/context-pii/`,
  `tests/integration/test_confidential_pass.py`, `tests/unit/test_confidential.py`,
  `tests/unit/test_confidential_recall.py`, `tests/unit/test_context_pii_fixture.py`.
- Changed, code: `__main__.py`, `config.py`, `detection/__init__.py`, `detection/addresses.py`,
  `detection/chunking.py`, `detection/gitleaks.py`, `detection/presidio.py`, `operator_lists.py`,
  `report.py`, `scrub.py`, `vault.py`, `scripts/e2e/media.py`.
- Changed, tests: `tests/conftest.py`, `tests/integration/test_history_stability.py`, `test_main.py`,
  `test_media.py`, `tests/unit/test_config.py`, `test_operator_lists.py`, `test_presidio_detector.py`,
  `test_report.py`, `test_scrub_leaf.py`, `test_sessions.py`, `test_spans.py`, `test_vault.py`.
- Changed, documents that say how to run and develop the code (T003): `README.md` § Install and
  § Configuration, 001 `plan.md` and `quickstart.md`, 023 `plan.md`.

## Governing documents

The requirements and contracts change in this spec PR, so they hold before the story starts:
- 001 `spec.md`: the scope paragraph, User Story 2, FR-012 to FR-015, the detected value's stage, SC-004's
  "With only the local pass", SC-005, SC-006, the assumption on accuracy targets, the Story 2 dependency.
- 001 `data-model.md`: `CONTEXT`, a span's stage, the leaf cache's detectors, the operator lists'
  categories, the scrub report's key.
- 001 `contracts/placeholder-format.md` § Outbound and `contracts/http-interface.md` § Errors;
  `contracts/confidential-detection.md` deleted, with the links to it in 001 `quickstart.md` and
  `research.md` and its entry in 001 `plan.md`.
- 023 `spec.md` FR-004.
- `README.md` § Features and § Limits, which `CLAUDE.md` makes requirements.

001's `research.md`, `tasks.md`, checklists and critiques, and 023's `tasks.md`, record what was decided and done
then, and stay.

## Testing

Privatemode is mocked with `respx`. T001's tests are written and run red before T002 changes the code; the
existing tests of local detection, restoration and media conversion stay as the regression net.
