# Implementation Plan: Claude's own replies are replayed as Anthropic sent them

**Spec**: [spec.md](spec.md) · **Issue**: #28 · **Depends on**: #31

## Summary

The session's leaf cache already maps a leaf's hash to the bytes sent for it. A reply now adds entries to
it: for each string of the reply, `sha256(string as restored) → string as Anthropic sent it`. A replayed
string is then a cache hit and never reaches a detector. No new matching, no new store, one code path for
streamed and non-streamed replies.

## Dependency on #31

#31 makes one rule decide which strings of model content are content, and the scrub and both restore
paths use it. This plan records exactly the strings that rule covers, so each recorded string is one
Claude Code received restored and one a replay would scrub. Implementation starts after #31 merges, on
the walk it leaves.

## Recording (`src/llm_pii_proxy/sessions.py`)

`Session.remember_reply(reply)` takes the reply as Anthropic sent it, in the shape of a non-streamed
response. For each string of `walker.collect_replayed_leaves(reply)`, `restore_text(string, self.vault)`
gives the restored text and its count of unissued tokens. A string with an unissued token is skipped
(spec edge cases, 001 FR-011). For every other string the key is `sha256(restored text)`, and unless that
key is in the leaf cache or pending, the cache maps it to the string (FR-004). Every key is computed
before the first entry is written, so a failure records nothing of the reply. The restored text itself is
not stored; only its hash is (FR-006).

After #31, both restore paths apply `restore_text`'s token-to-value rule to exactly the strings the
content rule covers. Non-streamed, nothing is awaited between restoring and recording, so each key will be
the hash of the string Claude Code got. Streamed, a string is restored earlier, delta by delta, and can
still come out differently at recording time, for example where the model wrote a token the vault issued
only after the stream had passed it. Such a string misses the cache when replayed and is scrubbed as today.
Either way the value sent for a key is text Anthropic wrote.

## Walker (`src/llm_pii_proxy/walker.py`)

`collect_replayed_leaves(reply) -> list[str]`: the strings of `reply["content"]` that the content rule
covers, in walk order. A message's `role` is not part of the reply, so it is not collected: a replay's
role string is scrubbed as today. A block with a binary source yields no strings instead of raising: on
replay it never reaches the leaf walk, because it is converted to text or the request is refused first
(specs/023-media-to-text).

## Where replies are recorded (`src/llm_pii_proxy/replies.py`, `app.py`)

`ReplyRecorder(session)`, one per `/v1/messages` response, is the only caller of `remember_reply`. One
guard in it wraps every step that reads reply content, feeding an event and recording: on failure it stops
recording that reply and logs the error type only, as `RequestTrace` does (`tracing/record.py`). The reply
is forwarded unchanged and a replay is scrubbed as today.

- Non-streamed: after `restore_message`, `recorder.record(reply)` with the parsed upstream body, then the
  response is returned (FR-002).
- Streamed: a stage between the upstream events and `restore_stream` passes each event to
  `recorder.feed(event)`, which feeds a `MessageAssembler` and, on `message_stop` with the assembler
  `complete`, records `assembler.message()`. The stage yields each event on only after `feed` returns, so
  Claude Code receives `message_stop` only after the reply is recorded (FR-002). A stream that ends before
  `message_stop`, or sends an `error` event before it, is not `complete` there, so nothing is recorded.

`MessageAssembler` moves from `tracing/assemble.py` to `src/llm_pii_proxy/assemble.py`, unchanged: the
request path now depends on it, and tracing is optional. `tracing/record.py` and `tests/unit/test_assemble.py`
import it from there.

## Governing documents

Changed in this spec PR: 001 `spec.md` FR-002, `contracts/http-interface.md` § Which strings are scrubbed,
`data-model.md` § Leaf cache, and README § Features.

## Testing

Unit tests for `collect_replayed_leaves`, `Session.remember_reply` and `ReplyRecorder`; integration tests
through the app with upstream mocked by `respx` and a stub detector that flags a span of the #28 script and
records every text it receives. No real model or detector is needed: the defect is which strings reach
detection, not what NER finds in them.

Two confidential-pass tests expect a replayed reply string to reach the pass
(`tests/integration/test_confidential_pass.py`); they change to the new expectation before the code does.
