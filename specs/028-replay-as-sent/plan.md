# Implementation Plan: Claude's own replies are replayed as Anthropic sent them

**Spec**: [spec.md](spec.md) · **Issues**: #28, #31

## Summary

Two changes, in this order, in one story:

1. **One content rule** (FR-007) in `walker.py`, used by the request scrub, the non-streamed restore, the
   streamed restore and recording (FR-008). Every string Claude Code receives restored is then one a replay
   scrubs, which closes #31.
2. **Replies recorded into the leaf cache.** The cache already maps a leaf's hash to the bytes sent for it.
   A reply adds `sha256(string as restored) → string as Anthropic sent it` for each string the rule covers.
   A replayed string is then a cache hit and never reaches a detector (#28). No new matching and no new
   store; one code path for streamed and non-streamed replies.

## Content rule (`src/llm_pii_proxy/walker.py`)

`_walk` gains one case: inside the value of an `input` key, every string, object keys included, goes to
`on_leaf`, and no skip-list, thinking or binary rule applies. Outside it, the walk is as today. A key goes to
`on_leaf` before its value, so `collect_leaves` and `apply_leaves` keep one order, and `apply_leaves`
rebuilds each object with its replaced keys in the same positions.

`walk_model_content(node, on_leaf)` applies the same `_walk` to model content of a reply, a whole `content`
list, one block or one delta, keeping a block with a binary source as it is instead of raising. It is the
only entry point for the reply side, so the scrub and every reply path share one rule.

## Restoring (`restore.py`, `sse.py`)

`restore_content(node, vault) -> (node, unissued)` is `walk_model_content` with `restore_text` as `on_leaf`.

- Non-streamed: `restore_message` restores `body["content"]` with `restore_content`.
- Streamed: `content_block_start` restores its `content_block` with `restore_content`. A
  `content_block_delta` of type `text_delta` or `input_json_delta` is restored incrementally by the
  holdback restorer as today; `thinking_delta` and `signature_delta` pass unchanged; any other delta is
  restored whole with `restore_content`. An event whose payload restores to itself is yielded as received,
  byte for byte. Unissued counts reach the report as today.

Two keys of one object that restore to the same text keep the last value on both paths: the dict built by
the walk, and the JSON text Claude Code parses, both keep the last.

## Recording (`src/llm_pii_proxy/sessions.py`)

`Session.remember_reply(reply)` takes the reply as Anthropic sent it, in the shape of a non-streamed
response. For each string of `walker.collect_replayed_leaves(reply)`, `restore_text(string, self.vault)`
gives the restored text and its count of unissued tokens. A string is skipped when it has an unissued
token (001 FR-011), or when Anthropic's string itself holds, in plain text, a value the vault holds (001
FR-018; the vault is seeded with the always-scrub list). That check is the scrub's exact known-value match
(`scrub._known_spans`), shared rather than copied. For every other string the key is
`sha256(restored text)`, and unless that key is in the leaf cache or pending, the cache maps it to the
string (FR-004). Entries are written in walk order, so within one reply the first string for a key wins.
Every key is computed before the first entry is written, so a failure records nothing of the reply. An
entry is a hash and Anthropic's string; the skip above keeps out every string in which the scrub would
find a vault value (FR-006).

Both restore paths apply `restore_text`'s token-to-value rule to exactly the strings the content rule
covers. Non-streamed, nothing is awaited between restoring and recording, so each key is the hash of the
string Claude Code got. Streamed, a string is restored earlier, delta by delta, and can still come out
differently at recording time, for example where the model wrote a token the vault issued only after the
stream had passed it. Such a string misses the cache when replayed and is scrubbed as today. Either way
the value sent for a key is text Anthropic wrote.

`collect_replayed_leaves(reply) -> list[str]` collects, with `walk_model_content`, the strings of
`reply["content"]` in walk order. Only `content` is walked (FR-001): the message's `role` is not recorded,
so a replay's role string is scrubbed as today.

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

Changed in this spec PR: 001 `spec.md` FR-002 and FR-005, `contracts/http-interface.md` § Which strings are
scrubbed and § Responses, `data-model.md` § Leaf cache and § Stream restoration state, and README § Features.

## Testing

Unit tests for the content rule on every path, `collect_replayed_leaves`, `Session.remember_reply` and
`ReplyRecorder`; integration tests through the app with upstream mocked by `respx` and a stub detector that
flags a span of the #28 script and records every text it receives. No real model or detector is needed: the
defects are which strings reach detection and restoration, not what NER finds in them.

Existing tests that expect the old rule change to the new expectation before the code does:
`test_walker.py::test_system_string_and_messages_at_any_depth` and `::test_keys_never_scrubbed` (keys inside
an `input` value are now scrubbed), and two confidential-pass tests that expect a replayed reply string to
reach the pass (`tests/integration/test_confidential_pass.py`).
