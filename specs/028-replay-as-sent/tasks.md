# Tasks: Claude's own replies are replayed as Anthropic sent them

**Input**: [spec.md](spec.md), [plan.md](plan.md)

**Tests**: Required. Write each test task, run it, and confirm it fails for the stated reason before the
task that makes it pass. Upstream is mocked with `respx`; tests never call real services.

## User Story 1 - Claude sees its own tool calls as it wrote them (#28)

- [ ] T001 [US1] Move `MessageAssembler` to `src/llm_pii_proxy/assemble.py` unchanged; update its imports
  in `tracing/record.py` and `tests/unit/test_assemble.py`. No behaviour change: the suite stays green.
- [ ] T002 [US1] Tests in `tests/unit/test_walker.py` for `collect_replayed_leaves`: text and `tool_use`
  input strings in order, `thinking` and `redacted_thinking` blocks and structural keys left out (as
  `collect_leaves`), a block with a binary source left out without raising. Red: function missing.
- [ ] T003 [US1] Add `collect_replayed_leaves` to `walker.py`. T002 green.
- [ ] T004 [US1] Tests in `tests/unit/test_sessions.py` for `Session.remember_reply`: a replayed leaf, with
  and without a placeholder, returns Anthropic's string from `scrub_leaves` and the pipeline is not called
  for it; an existing cache entry and a pending scrub for the same text keep theirs; a leaf of the reply
  that Claude Code changed is scrubbed; a `tool_use` input whose `type` value is a token restoring to
  `thinking` still records its strings. Red: method missing.
- [ ] T005 [US1] Implement `Session.remember_reply` (plan.md § Recording). T004 green.
- [ ] T006 [US1] Integration tests in `tests/integration/test_replay.py`, through the app with upstream
  mocked and a stub detector flagging a span of the #28 script: spec acceptance scenarios 1–5 and the edge
  cases (stream ending before `message_stop`, `error` event before it, non-2xx, `count_tokens` replay), streamed and
  non-streamed; SC-001 byte-identity of the replayed `tool_use` input; with the confidential pass enabled,
  it receives no replayed string; the reply is recorded before `message_stop` reaches the client; a
  stream event the assembler cannot read leaves the reply unchanged for the client, records nothing and
  logs the error type without content. Red:
  `app.py` records nothing.
- [ ] T007 [US1] Wire recording into `app.py` (plan.md § Where replies are recorded); README § Features:
  Claude's own replies are replayed as Anthropic sent them, not scrubbed again. T006 green.
- [ ] T008 Run `uv run pytest` and the lint command in CLAUDE.md; all green, `test_no_leak.py` unchanged
  (SC-002).
