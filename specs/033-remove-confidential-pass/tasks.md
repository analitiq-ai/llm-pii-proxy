# Tasks: Remove the confidential pass

**Input**: [spec.md](spec.md), [plan.md](plan.md)

**Tests**: Required. Write each test task, run it, and confirm it fails for the stated reason before the
task that makes it pass. Privatemode is mocked with `respx`; tests never call real services.

## User Story 1 - The proxy scrubs with local detection only (#33)

- [ ] T001 [US1] Change the tests first:
  - In `tests/unit/test_config.py`, expect `privatemode_url` and `privatemode_timeout_s` with the defaults and
    checks of the settings they replace, and no `confidential_*` setting. Set `PII_PROXY_CONFIDENTIAL_URL` and
    `PII_PROXY_CONFIDENTIAL_TIMEOUT_S` and expect both defaults; set `PII_PROXY_PRIVATEMODE_URL` and
    `PII_PROXY_PRIVATEMODE_TIMEOUT_S` and expect their values (FR-002, Edge Cases).
  - In `tests/unit/test_vault.py`, drop `CONTEXT` from the expected category list (FR-001).
  - In `tests/unit/test_report.py` and `test_scrub_leaf.py`, call `add` with a category and a count, key the
    expected `counts` by category, and drop `confidential_unlocated` from the expected log line (FR-001,
    SC-002).
  - In `tests/integration/test_main.py`, replace the confidential-pass test with one that sets
    `PII_PROXY_CONFIDENTIAL_ENABLED=true`, and `PII_PROXY_CONFIDENTIAL_URL` and `PII_PROXY_PRIVATEMODE_URL` to
    the mock, and records the `base_url` of each `httpx.AsyncClient` that `build_app` creates. With media
    conversion disabled, expect no client for the mock and nothing sent to it; with it enabled, expect one
    client for the mock and nothing sent to it for a request without an image or PDF (acceptance scenarios 1
    and 2, Edge Cases, FR-003). In the media test, set `PII_PROXY_PRIVATEMODE_URL` in the environment instead
    of `confidential_url` in `model_copy` (acceptance scenario 3, FR-002).

  Red: the `PII_PROXY_PRIVATEMODE_*` settings do not exist and the `PII_PROXY_CONFIDENTIAL_*` ones are read;
  `CONTEXT` exists; `counts` and `add` carry a stage; the log line has the count; with media conversion
  disabled, `build_app` creates a client for the mock and the confidential pass calls it; `build_app` reads
  `confidential_url`, so the media test's image goes to the default URL.
- [ ] T002 [US1] Remove the confidential pass and rename the two settings (plan.md § Removed, § Settings).
  Delete the files plan.md lists, drop the stage from every span the tests build, drop the `CONTEXT` case
  from `test_operator_lists.py` (`NOSUCH` covers an unknown category), and delete
  `test_converted_text_reaches_the_confidential_pass` from `test_media.py`. Give the single-flight comment in
  `sessions.py` the reason data-model.md § Leaf cache gives. T001 passes.
- [ ] T003 [US1] Update the documents that say how to run and develop the code (plan.md § Files the story
  changes): no confidential pass, and the `PII_PROXY_PRIVATEMODE_*` names.
