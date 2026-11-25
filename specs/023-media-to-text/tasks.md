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
  through and `skipped_binary == 3`, instead expects `collect_leaves` to raise. Red: functions missing,
  walker still skips.
- [ ] T004 [US1] Change `walker.py` and `report.py` (`skipped_binary` out, `media_converted` in). T003 green.
- [ ] T005 [US1] Tests in `tests/unit/test_media.py` for `media.py`, one per row of plan.md § Conversion,
  plus: replacement text format for image (document and not) and PDF (with and without `title`, page
  order kept under concurrency); `cache_control` carried over; the same block twice converts once and
  yields identical text; a failed conversion is not cached; LRU eviction at `media_cache_entries`; at
  most `media_concurrency` calls in flight; OCR and vision request bodies as plan.md states; timeout,
  transport error and 500 → 503, an unparseable vision answer → 502, causes without content. Red: module
  missing.
- [ ] T006 [US1] Implement `src/llm_pii_proxy/media.py`. T005 green.
- [ ] T007 [US1] Integration tests in `tests/integration/`, through the app with upstream and Privatemode
  mocked: spec acceptance scenarios 1–8 and the edge cases, for `messages` and `count_tokens`; the upstream
  body holds no binary block and no seeded value; the report header shows `media_converted`; startup fails
  with `media_enabled` and no `pdftoppm` on `PATH`. Red: `_scrub` does not convert, and `build_app` does
  not check for `pdftoppm`.
- [ ] T008 [US1] Wire conversion into `App._scrub` and the Privatemode client into `build_app`
  (`__main__.py`). T007 green.
- [ ] T009 [US1] `scripts/e2e/media.py` (plan.md § Testing); run it by hand once for SC-001 and SC-002.
- [ ] T010 [US1] README: features, settings, `pdftoppm` requirement.
- [ ] T011 Run `uv run pytest` and the lint command in CLAUDE.md; all green.
