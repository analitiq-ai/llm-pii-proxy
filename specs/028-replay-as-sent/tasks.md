# Tasks: Claude's own replies are replayed as Anthropic sent them

**Input**: [spec.md](spec.md), [plan.md](plan.md)

**Prerequisite**: #31 merged.

**Tests**: Required. Write each test task, run it, and confirm it fails for the stated reason before the
task that makes it pass. Upstream is mocked with `respx`; tests never call real services.

## User Story 1 - Claude sees its own tool calls as it wrote them (#28)

- [ ] T001 [US1] Move `MessageAssembler` to `src/llm_pii_proxy/assemble.py` unchanged; update its imports
  in `tracing/record.py` and `tests/unit/test_assemble.py`. No behaviour change: the suite stays green.
- [ ] T002 [US1] Tests in `tests/unit/test_walker.py` for `collect_replayed_leaves`: the strings of
  `content` the content rule (#31) covers, in walk order, text and `tool_use` input values and keys;
  no `role`; `thinking` and `redacted_thinking` blocks and structural keys left out; a block with a binary
  source left out without raising. Red: function missing.
- [ ] T003 [US1] Add `collect_replayed_leaves` to `walker.py`. T002 green.
- [ ] T004 [US1] Tests in `tests/unit/test_sessions.py` for `Session.remember_reply`: a replayed leaf, with
  and without a placeholder, returns Anthropic's string from `scrub_leaves` and the pipeline is not called
  for it; an existing cache entry and a pending scrub for the same text keep theirs; a leaf of the reply
  that Claude Code changed is scrubbed; a string holding an unissued token is not recorded, and its replay
  is escaped as `LITERAL`; a failure while computing keys records nothing of the reply. Red: method
  missing.
- [ ] T005 [US1] Implement `Session.remember_reply` (plan.md § Recording). T004 green.
- [ ] T006 [US1] Tests in `tests/unit/test_replies.py` for `ReplyRecorder`: `feed` records on
  `message_stop` only when the assembler is `complete`; `record` and `feed` record nothing after an
  event the assembler cannot read or an error in `remember_reply`, never raise, and log the error type
  without content. Red: module missing.
- [ ] T007 [US1] Implement `ReplyRecorder` (plan.md § Where replies are recorded). T006 green.
- [ ] T008 [US1] Change the expectations of `test_privatemode_sees_placeholders_never_vault_values` and
  `test_second_turn_sends_only_new_leaves` in `tests/integration/test_confidential_pass.py`: the replayed
  reply string `ok` no longer reaches the pass, the new `assistant` role string still does. Red: `app.py`
  records nothing.
- [ ] T009 [US1] Integration tests in `tests/integration/test_replay.py`, through the app with upstream
  mocked and a stub detector flagging a span of the #28 script: spec acceptance scenarios 1–5 and the edge
  cases (stream ending before `message_stop`, `error` event before it, non-2xx, unissued token,
  `count_tokens` replay), streamed and non-streamed; SC-001 byte-identity of the replayed `tool_use`
  input; with the confidential pass enabled, it receives no replayed string; the reply is recorded before
  `message_stop` reaches the client; a recording failure on either path leaves the reply unchanged for the
  client and records nothing. Red: `app.py` records nothing.
- [ ] T010 [US1] Wire `ReplyRecorder` into `app.py` (plan.md § Where replies are recorded). T008 and T009
  green.
- [ ] T011 Run `uv run pytest` and the lint command in CLAUDE.md; all green, `test_no_leak.py` unchanged
  (SC-002).
