# Implementation Plan: Claude's own replies are replayed as Anthropic sent them

**Spec**: [spec.md](spec.md) · **Issue**: #28

## Summary

The session's leaf cache already maps a leaf's hash to the bytes sent for it. A reply now adds entries to
it: for each string a replay of the reply would hold, `sha256(string as restored) → string as Anthropic
sent it`. A replayed string is then a cache hit and never reaches a detector. No new matching, no new
store, one code path for streamed and non-streamed replies.

## Recording (`src/llm_pii_proxy/sessions.py`)

`Session.remember_reply(reply)` takes the reply as Anthropic sent it, in the shape of a non-streamed
response. For each string of `walker.collect_replayed_leaves(reply)`, the key is
`sha256(restore_text(string, self.vault))`, and unless that key is in the leaf cache or pending, the cache
maps it to the string (FR-004). Every key is computed before the first entry is written, so a failure
records nothing of the reply. The restored text itself is not stored; only its hash is (FR-006).

Only Anthropic's reply is walked; its restored copy never is, so a restored value cannot change which
strings are found.

Every string `collect_replayed_leaves` finds lies in `content` outside a thinking block, which is where
`restore_message` applies `restore_text` to a non-streamed reply. Nothing is awaited between restoring
and recording it, so each key is the hash of the string Claude Code got. A
streamed reply was restored earlier, delta by delta, by `restore_stream` with the same token-to-value
rule; a string can still come out differently at recording time, for example where the model wrote a
token the vault issued only after the stream had passed it. Such a string misses the cache when replayed
and is scrubbed as today. Either way the value sent for a key is text Anthropic wrote.

## Walker (`src/llm_pii_proxy/walker.py`)

`collect_replayed_leaves(reply) -> list[str]`: the leaves of
`{"messages": [{"role": "assistant", "content": reply["content"]}]}`, the shape Claude Code replays a reply
in, found by the same `_walk_fields` rules as `collect_leaves`. A block with a binary source yields no
leaves instead of raising: on replay it never reaches the leaf walk, because it is converted to text or the
request is refused first (specs/023-media-to-text).

## Where replies are recorded (`src/llm_pii_proxy/app.py`)

- Non-streamed: after `restore_message`, `session.remember_reply(json.loads(content))`, then the response
  is returned (FR-002).
- Streamed: one stage reads the upstream events before `restore_stream` and feeds a `MessageAssembler`.
  When it receives `message_stop` and the assembler is `complete`, it calls
  `session.remember_reply(assembler.message())`, then yields `message_stop` on. Claude Code therefore
  receives `message_stop` only after the reply is recorded (FR-002). A stream that ends before
  `message_stop`, or sends an `error` event before it, is not `complete` there, so nothing is recorded.
- A recording failure, in the assembler or in `remember_reply`, stays out of the reply, as a trace
  failure does (`tracing/record.py`): the stage stops recording that reply, logs the error type only, and
  keeps forwarding events. A replay is then scrubbed as today.

`MessageAssembler` moves from `tracing/assemble.py` to `src/llm_pii_proxy/assemble.py`, unchanged: the
request path now depends on it, and tracing is optional. `tracing/record.py` and `tests/unit/test_assemble.py`
import it from there.

## 001 documents

Changed in this spec PR: `contracts/http-interface.md` § Which strings are scrubbed (replayed reply
strings) and `data-model.md` § Leaf cache (entries recorded from replies). README § Features changes with
the code (T007).

## Testing

Unit tests for `collect_replayed_leaves` and `Session.remember_reply`; integration tests through the app
with upstream mocked by `respx` and a stub detector that flags a span of the #28 script and records every
text it receives. No real model or detector is needed: the defect is which strings reach detection, not
what NER finds in them.
