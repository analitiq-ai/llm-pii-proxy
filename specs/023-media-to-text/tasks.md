# Tasks: Images and PDFs become scrubbed text

**Input**: [spec.md](spec.md), [plan.md](plan.md)

**Tests**: Required. Write each test task, run it, and confirm it fails for the stated reason before the
task that makes it pass. Privatemode is mocked with `respx`; tests never call real services.

## User Story 1 - Share an image or PDF without its bytes leaving the machine (#23)

- [ ] T001 [US1] Tests in `tests/unit/test_config.py`: the five `media_*` settings' defaults and bounds
  (plan.md § Settings). Red: fields missing.
- [ ] T002 [US1] Add the settings to `src/llm_pii_proxy/config.py`. T001 green.
- [ ] T003 [US1] Change `tests/unit/test_walker.py`. Add tests for `collect_binary_blocks` and
  `apply_binary_blocks`: blocks found in order, inside `tool_result` and inside a `content`-source
  document, thinking blocks untouched. Its binary-source case, which today expects the blocks passed
  through and `skipped_binary == 3`, instead expects `collect_leaves` to raise. In
  `tests/unit/test_report.py`, `skipped_binary` becomes `media_converted`. Red: functions missing, walker
  still skips, report field missing.
- [ ] T004 [US1] Change `walker.py`, `report.py` and `app.py` (`skipped_binary` out, `media_converted` in),
  and `specs/001-pii-scrubbing-proxy/data-model.md` § Scrub report to match. T003 green.
- [ ] T004a [US1] Tests in `tests/unit/test_privatemode.py`, one per outcome of `privatemode.complete` in
  plan.md § Conversion, each asserting the error type and a cause naming the purpose and that failure
  kind, never content. In `tests/unit/test_confidential.py`, a `finish_reason` of `length` → 502 whose
  cause names the confidential pass and a cut-off answer. Red: module missing, confidential pass accepts
  a cut-off answer.
- [ ] T004b [US1] Add `src/llm_pii_proxy/privatemode.py`; `detection/confidential.py` calls it; rename the
  two error types in `errors.py` and every use; `contracts/confidential-detection.md` fail-closed row
  gains the cut-off answer. T004a green.
- [ ] T005 [US1] Tests in `tests/unit/test_media.py` for `media.py`, one per row of plan.md § Conversion
  (the malformed row once per condition, including base64 with a non-alphabet character; each cause
  without the data), plus: the row order (conversion disabled and an unsupported media type → the
  `PII_PROXY_MEDIA_ENABLED` 400); a valid block followed by an invalid one → 400 with no Privatemode call
  and nothing cached; replacement text format for image (document and not) and PDF (with and without `title`, page
  order kept under concurrency); `cache_control` carried over; the same block twice converts once and
  yields identical text; overlapping requests share one conversion, and one going away does not cancel
  it; a failed conversion is not cached and leaves the in-flight registry, and one no request receives is
  logged without content; LRU eviction at `media_cache_entries`, an evicted block converting again; at
  most `media_concurrency` calls in flight; OCR and vision request bodies as plan.md states; timeout,
  transport error and 500 → 503, an unparseable vision answer or a `finish_reason` other than `stop` →
  502 and not cached, a render timeout → 503 with the render process killed; causes name the purpose and failure kind
  (plan.md § Conversion), never content.
  Red: module missing.
- [ ] T006 [US1] Implement `src/llm_pii_proxy/media.py` and add `MediaRenderTimedOut` to `errors.py`.
  T005 green.
- [ ] T007 [US1] Integration tests in `tests/integration/`, through the app with upstream and Privatemode
  mocked: spec acceptance scenarios 1–8 and the edge cases, for `messages` and `count_tokens`; the upstream
  body holds no binary block and no seeded value; with the confidential pass enabled, converted text goes
  through it; nothing logged holds converted content (`caplog`); the report header shows
  `media_converted`; startup fails
  with `media_enabled` and no `pdftoppm` on `PATH`. Red: `_scrub` does not convert, and `build_app` does
  not check for `pdftoppm`.
- [ ] T008 [US1] Wire conversion into `App._scrub` and the Privatemode client into `build_app`
  (`__main__.py`). T007 green.
- [ ] T009 [US1] `scripts/e2e/media.py` (plan.md § Testing); run it by hand once for SC-001 and SC-002.
- [ ] T010 [US1] README: features, `pdftoppm` requirement; the five settings in
  `specs/001-pii-scrubbing-proxy/quickstart.md`.
- [ ] T011 Run `uv run pytest` and the lint command in CLAUDE.md; all green.
