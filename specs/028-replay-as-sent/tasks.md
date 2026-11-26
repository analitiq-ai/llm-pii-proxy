# Tasks: Claude's own replies are replayed as Anthropic sent them

**Input**: [spec.md](spec.md), [plan.md](plan.md)

**Tests**: Required. Write each test task, run it, and confirm it fails for the stated reason before the
task that makes it pass. Upstream is mocked with `respx`; tests never call real services.

## User Story 1 - Claude sees its own tool calls as it wrote them (#28)

### Foundational: one content rule (#31)

- [ ] T001 [US1] Integration tests in `tests/integration/test_content_rule.py`, through the app with upstream
  mocked: acceptance scenario 6 and SC-003, a replayed `tool_use` input holding restored values under
  `name`, `type` and `id`, in a nested `thinking`-typed object and as an object key reaches upstream with
  placeholders for all of them, streamed and non-streamed; acceptance scenario 7, the same reply with a
  citation and a server tool result reaches the client with the same restored strings streamed and
  non-streamed. Red: the walk skips those strings and the stream leaves citations and block starts
  unrestored.
- [ ] T002 [US1] Change `test_walker.py::test_system_string_and_messages_at_any_depth` and
  `::test_keys_never_scrubbed` to expect keys inside an `input` value scrubbed; add walker tests: inside an
  `input` value, values under `name`, `type` and `id` and a nested `thinking`-typed object are scrubbed;
  outside it, the skip-list and thinking rule hold as today; `walk_model_content` keeps a binary block
  without raising. Red: the walk skips them and `walk_model_content` is missing.
- [ ] T003 [US1] Implement the content rule and `walk_model_content` in `walker.py` (plan.md § Content
  rule). T002 green.
- [ ] T004 [US1] Add tests to `tests/unit/test_restore.py` for `restore_content` and `restore_message`: keys
  and values under structural keys inside an `input` value restored; values of structural keys outside it not
  restored; thinking blocks untouched; a binary block kept. Red: `restore_content` missing.
- [ ] T005 [US1] Implement `restore_content`; `restore_message` uses it. T004 green.
- [ ] T006 [US1] Add tests to `tests/unit/test_sse.py`: a `content_block_start` block restored by the rule
  (a server tool result, a `tool_use` with non-empty input); a `citations_delta` restored whole;
  `thinking_delta` and `signature_delta` unchanged; an event in which nothing is restored forwarded
  byte-identical; unissued tokens of whole-restored events counted. Red: those events pass unrestored.
- [ ] T007 [US1] Restore `content_block_start` and other deltas in `sse.py` (plan.md § Restoring). T006 and
  T001 green.

### Recording replies (#28)

- [ ] T008 [US1] Move `MessageAssembler` to `src/llm_pii_proxy/assemble.py` unchanged; update its imports
  in `tracing/record.py` and `tests/unit/test_assemble.py`. No behaviour change: the suite stays green.
- [ ] T009 [US1] Add tests to `tests/unit/test_walker.py` for `collect_replayed_leaves`: the strings of
  `content` the content rule covers, in walk order, text and `tool_use` input values and keys, including
  values under `name`, `type` and `id`, a nested `thinking`-typed object and a placeholder object key
  inside the input; no `role`; `thinking` and `redacted_thinking` blocks and block-level structural keys
  left out; a block with a binary source left out without raising. Red: function missing.
- [ ] T010 [US1] Add `collect_replayed_leaves` to `walker.py`. T009 green.
- [ ] T011 [US1] Add tests to `tests/unit/test_sessions.py` for `Session.remember_reply`: a replayed leaf,
  with and without a placeholder, returns Anthropic's string from `scrub_leaves` and the pipeline is not
  called for it; an existing cache entry and a pending scrub for the same text keep theirs; a leaf of the
  reply that Claude Code changed is scrubbed; a string holding an unissued token is not recorded, and its
  replay is escaped as `LITERAL`; a string holding a vault value, or an always-scrub value, in plain text is
  not recorded, while its placeholder version is; within one reply the first string for a key is kept; a
  failure while computing keys records nothing of the reply. Red: method missing.
- [ ] T012 [US1] Implement `Session.remember_reply` (plan.md § Recording). T011 green.
- [ ] T013 [US1] Tests in `tests/unit/test_replies.py` for `ReplyRecorder`: `feed` records on
  `message_stop` only when the assembler is `complete`; `record` and `feed` record nothing after an
  event the assembler cannot read or an error in `remember_reply`, never raise, and log the error type
  without content. Red: module missing.
- [ ] T014 [US1] Implement `ReplyRecorder` (plan.md § Where replies are recorded). T013 green.
- [ ] T015 [US1] Change the expectations of `test_privatemode_sees_placeholders_never_vault_values` and
  `test_second_turn_sends_only_new_leaves` in `tests/integration/test_confidential_pass.py`: the replayed
  reply string `ok` no longer reaches the pass, the new `assistant` role string still does. Red: `app.py`
  records nothing.
- [ ] T016 [US1] Integration tests in `tests/integration/test_replay.py`, through the app with upstream
  mocked and a stub detector flagging a span of the #28 script: spec acceptance scenarios 1–5 and the edge
  cases (stream ending before `message_stop`, `error` event before it, non-2xx, unissued token, plain
  always-scrub value, `count_tokens` replay), streamed and non-streamed; SC-001 byte-identity of the
  replayed `tool_use` input, including values under `name`, `type` and `id`, a nested `thinking`-typed
  object and a placeholder key, with no detector call; with the confidential pass enabled, it receives no
  recorded content string; the reply is recorded before `message_stop` reaches the client; a recording
  failure on either path leaves the reply unchanged for the client and records nothing. Red: `app.py`
  records nothing.
- [ ] T017 [US1] Wire `ReplyRecorder` into `app.py` (plan.md § Where replies are recorded). T015 and T016
  green.
- [ ] T018 Run `uv run pytest` and the lint command in CLAUDE.md; all green, `test_no_leak.py` unchanged
  (SC-002).
