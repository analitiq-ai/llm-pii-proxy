# Feature Specification: Claude's own replies are replayed as Anthropic sent them

**Feature Branch**: `28-us1-replay-as-sent`

**Created**: 2026-11-26

**Status**: Draft

**Input**: Issue #28. Code Claude wrote, replayed in history as a tool call's input, is scrubbed again on
every later request. NER false positives garble it, Anthropic receives a broken copy of the model's own
tool call, and the model rewrites correct files.

Decided with the user (2026-11-26): fix the replay only (#28 direction 1), with no regex and no
per-scenario handling. Detection false positives on code that does not come from the model (#28 directions
2 and 3) move to #29. Review found that the strings the proxy restores and the strings the scrub covers
differ, which already sends real values upstream on replay; that moves to #31, which lands first
(Assumptions).

## Root cause

The request path re-derives model-authored text by detection instead of inverting the restoration it did
itself. A reply's strings come from Anthropic; the proxy restores their placeholders on the way back.
When Claude Code replays the reply, the walk (contracts/http-interface.md § Which strings are scrubbed)
treats the restored strings as new content and runs every detector on them. Detection is not the inverse
of restoration, so each false positive turns Anthropic's text into text Anthropic never wrote, and the
leaf cache then fixes that text for the rest of the session.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Claude sees its own tool calls as it wrote them (Priority: P1)

A developer works with Claude Code through the proxy. Claude writes a script with the Write tool, runs a
command, answers in prose. On every later request, each string of those replies that the proxy recorded
(FR-001) reaches Anthropic exactly as Anthropic produced it, placeholders included, however the detectors
would have judged the restored text.

**Why this priority**: today Claude is shown corrupted copies of its own output, rewrites correct files,
and tells the user its output is garbled.

**Independent Test**: a mocked upstream replies with a `tool_use` whose input holds the script from #28
and a placeholder; a stub detector flags a span of that script. The next request replays the restored
reply. The upstream body holds the reply's strings exactly as the mocked upstream sent them, and the
detector never received them.

**Acceptance Scenarios**:

1. **Given** a non-streamed reply, **When** Claude Code replays it unchanged in the next request, **Then**
   each of its recorded strings (FR-001) reaches Anthropic exactly as Anthropic sent it, and no detector
   or confidential pass receives it.
2. **Given** a streamed reply that ended with `message_stop`, **When** Claude Code replays it unchanged,
   **Then** the same holds.
3. **Given** a recorded reply string that held placeholders the vault had issued, **When** it is
   replayed, **Then** Anthropic receives the placeholders it wrote, not new ones.
4. **Given** a recorded reply string without placeholders, **When** it is replayed, **Then** it too
   reaches Anthropic unchanged.
5. **Given** a later request holding user text, tool results, or a reply string Claude Code changed,
   **When** it is sent, **Then** those strings are scrubbed as today.

### Edge Cases

- A stream that ends before `message_stop`, or sends an `error` event before it, records nothing: its
  strings are scrubbed as today if replayed.
- A streamed reply string can restore differently when recorded than when it was streamed, for example a
  token the model wrote that the vault issued only after the stream passed it. That string misses the
  cache when replayed and is scrubbed as today.
- A non-2xx response records nothing.
- If recording fails, streamed or not (a stream event the assembler cannot read, an error while
  recording), the reply still reaches Claude Code unchanged; nothing of it is recorded, the failure is
  logged with its error type only, and a replay is scrubbed as today.
- A string the session already holds in its leaf cache, or is scrubbing, keeps its entry: bytes already
  sent are never changed (data-model.md § Leaf cache).
- After a proxy restart or session eviction nothing is recorded, so replayed history is scrubbed as today.
- A `thinking` or `redacted_thinking` block is untouched on both paths, as today.
- A block in a reply with a binary source (a document a server tool fetched) records nothing: on replay it
  is converted to text or the request is refused before the scrub (specs/023-media-to-text).
- A reply string holding a token the vault has not issued is not recorded. Recorded verbatim, the vault
  could later issue that token for a real value, and one token would stand for two things (001 FR-011). A
  replay of it is scrubbed as today, the token escaped as `LITERAL`.
- A reply string that holds, in plain text, a value the vault holds is not recorded. The vault is seeded
  with the always-scrub list, so this covers those values too, which 001 FR-018 says are always replaced.
  A replay of it is scrubbed as today. Only the placeholder version of such a string can then be recorded,
  so one key never serves both.
- Once #31 has merged, a string its content rule leaves out, such as a block's `id`, is neither restored
  nor scrubbed, so it is replayed as Anthropic sent it without being recorded.
- `count_tokens` requests use the same leaf cache, so a replayed reply counts as it will be sent.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: For each successful `/v1/messages` reply, streamed or not, the session records every string
  of the reply's `content` that the content rule (#31) covers: the hash of the string restored with the
  session's vault maps to the string as Anthropic sent it. A string whose restore meets a token the vault
  has not issued, or that holds in plain text a value the vault holds, is not recorded. A streamed reply
  is recorded only once its stream completes.
- **FR-002**: A reply is recorded before its last byte reaches Claude Code: before `message_stop` is
  forwarded, or before the non-streamed body is returned. Claude Code cannot replay a reply it has not
  received in full.
- **FR-003**: A leaf whose hash is recorded is sent as recorded. No detector and no confidential pass
  receives it.
- **FR-004**: Recording never replaces a leaf cache entry or a pending scrub; within one reply, the first
  string for a key is kept.
- **FR-005**: A leaf not recorded is scrubbed as today, fail closed.
- **FR-006**: Recording stores no value the vault holds: the key is a hash, and the value is Anthropic's
  text, which holds placeholders where the restored text holds vault values (FR-001). No string of the
  reply is logged; a recording failure logs its error type only.

## Success Criteria *(mandatory)*

- **SC-001**: With a detector that flags spans in the #28 script, a replayed `tool_use` writing that script
  reaches upstream byte-identical to the upstream reply that produced it, streamed and non-streamed.
- **SC-002**: No string the proxy did not return in a reply skips the scrub: `tests/integration/test_no_leak.py`
  passes unchanged, and acceptance scenario 5 is tested.

## Assumptions

- Implementation follows #31, which will make one rule decide which strings of model content are
  content, for the scrub and both restore paths. Every string recorded is then one Claude Code received
  restored and one a replay would scrub.
- Claude Code replays a reply's strings as it received them: text blocks as streamed, tool inputs as the
  JSON it parsed. A string it changes is a cache miss and is scrubbed.
- Sending Anthropic its own text leaks nothing: every value in it is either a placeholder or text the
  model wrote.
