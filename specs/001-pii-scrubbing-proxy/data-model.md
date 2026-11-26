# Data Model: PII-Scrubbing Proxy

Everything here lives in process memory. Nothing is written to disk or logged (FR-008, FR-009).

## Category

A closed enumeration. The name appears inside placeholders, so it is uppercase ASCII letters only, with no
underscores.

| Category | Source | Covers |
|---|---|---|
| `PERSON` | local NER | personal names |
| `EMAIL` | local pattern | email addresses |
| `PHONE` | local pattern | phone numbers |
| `ADDRESS` | local NER (location) | postal addresses and locations |
| `CARD` | local pattern + checksum | payment card numbers |
| `IBAN` | local pattern + checksum | bank account numbers |
| `NATIONALID` | local pattern | national ID numbers (SSN, etc.) |
| `IP` | local pattern | IPv4 / IPv6 addresses |
| `SECRET` | local secret rules | API keys, tokens, passwords, private keys, credentialed connection strings |
| `CONTEXT` | confidential pass | context-dependent PII flagged by the second pass |
| `LITERAL` | escaping | pre-existing text in the user's data that matches the placeholder grammar (FR-011) |

## Placeholder

The stand-in for one original value. Grammar: [contracts/placeholder-format.md](contracts/placeholder-format.md).

| Field | Type | Rule |
|---|---|---|
| `category` | Category | |
| `ordinal` | int, 1–9999 | per category, assigned in first-seen order, never reused. Exhaustion fails the leaf closed |
| `token` | str | `PII_<CATEGORY>_<ordinal as 4 digits>`, derived, never stored separately |

## Session

One Claude Code session, identified by the `x-claude-code-session-id` request header. It includes that
session's subagents, which share the session id and differ only in `x-claude-code-agent-id`. A session is the
spec's "conversation" scope.

| Field | Type | Rule |
|---|---|---|
| `session_id` | str | from the header. A content-bearing request without it is rejected (http-interface.md) |
| `vault` | Vault | |
| `leaf_cache` | Leaf cache | |
| `last_seen` | timestamp | for idle eviction |

Lifecycle: created on the first request carrying a new session id. Its vault is seeded with the operator's
always-scrub values, in file order, so their ordinals are the same in every session. Evicted as a whole once idle longer than
`PII_PROXY_SESSION_IDLE_TTL_S` (default 43 200 = 12 h). It is never partly evicted, because that would change
the bytes of re-sent history (see Leaf cache). Process restart drops every session.

## Vault (the mapping)

Per session: two-way association between original values and placeholders.

| Field | Type | Rule |
|---|---|---|
| `by_value` | map[str → Placeholder] | key is the exact original string |
| `by_token` | map[str → str] | uppercase token → original value |
| `next_ordinal` | map[Category → int] | |

Invariants:
- Append-only. An entry is never changed or removed while the session lives.
- `by_value` and `by_token` are exact inverses.
- A value has one placeholder. If two detectors disagree on its category, the first assignment wins.
- Mutations happen only on the event-loop thread. Detection runs in worker threads and returns spans; it never
  touches the vault.

## DetectedSpan

Output of a detector, the input to placeholder assignment.

| Field | Type | Rule |
|---|---|---|
| `start`, `end` | int | offsets into the text it was detected in; `0 ≤ start < end ≤ len(text)` |
| `category` | Category | |
| `stage` | `local` \| `confidential` | used only for the scrub report |

Edge trimming: before assignment, a span is shrunk to exclude leading and trailing whitespace and the
delimiters `, ; | " '` and tab, and it is split at line breaks. A detector that swallows a CSV comma or a row
number would otherwise remove structure from what the model sees (research.md R0).

Overlap resolution: when spans overlap, the longest span wins, then the earliest start. The result is a set of
non-overlapping spans.

## Text leaf

One string value inside a request body that may carry user content. Which leaves are scanned is defined in
[contracts/http-interface.md](contracts/http-interface.md#which-strings-are-scrubbed).


## Leaf cache

Per session: raw leaf text → the text sent for it.

| Field | Type | Rule |
|---|---|---|
| key | sha256 of the raw leaf text | for a reply entry, the string as restored: what a replay sends |
| value | the scrubbed leaf text | for a reply entry, the string as Anthropic sent it |

This is required for **correctness**, not only for speed. A `thinking` block's signature fingerprints every
byte sent before it (research.md R3). Each re-sent history leaf must therefore scrub to exactly the bytes sent
the first time, even though the vault has grown since and the detectors (NER, the confidential pass) aren't
guaranteed to repeat themselves. It is also what makes FR-012 and SC-004 achievable: only cache-miss leaves are
analysed, and only those go to the confidential pass.

**Entries recorded from replies** ([specs/028-replay-as-sent](../028-replay-as-sent/spec.md)): for each
successful `/v1/messages` reply, before its last byte reaches the client, the session MUST record every
string of its `content` that the scrub covers, with key the sha256 of the string restored with the
session's vault and value the string as Anthropic sent it, so a replay of it is a cache hit and reaches no
detector. It MUST NOT record a streamed reply whose stream did not complete, a string whose restore meets a
token the vault has not issued, a string that holds in plain text a value the vault holds, or anything over
an existing entry or a pending scrub.

No LRU eviction. Memory is bounded by the session's own history size and released when the session is evicted.

**Single-flight**: concurrent requests in one session (parallel subagents re-send the same system and CLAUDE.md
leaves) must not scrub the same leaf twice, because a non-deterministic detector could give each a different
result and one of them would then re-send bytes the cache doesn't hold. The first request to miss a leaf
registers a pending entry; any other request needing that leaf awaits it.
- Success: the leaf's bytes are cached as soon as **that leaf** finishes, even if another leaf in the same
  request later fails. Once computed, they are the leaf's bytes for the life of the session.
- Failure: the pending entry is removed and every awaiting request fails closed with the same error. Nothing is
  cached, so the next attempt scrubs the leaf afresh.

## Operator lists (FR-018)

Loaded once at startup from `PII_PROXY_LISTS_PATH`. Shared by all sessions, read-only.

| Field | Type | Rule |
|---|---|---|
| `always_scrub` | map[Category → list[str]] | `LITERAL` and `CONTEXT` not allowed. Seeds each new vault |
| `never_scrub` | list[str] | exact, case-sensitive. Drops matching detected spans (placeholder-format.md, stage 3). Merged with the built-in list: `Claude`, `claude`, `Claude Code`, `Claude Agent SDK`, `Anthropic`, `Claude Opus`, `Claude Sonnet`, `Claude Haiku`, `Claude Fable` (research R3). An `always_scrub` value still wins: known values are matched before detection |

A value on both lists, an empty string, or an unknown category fails startup.

## Scrub report (FR-017)

Per request: counts by `(category, stage)`, plus the count of image and PDF blocks converted to text ([023](../023-media-to-text/plan.md)). It holds
no values or tokens. It is returned as response header `x-pii-proxy-scrubbed: EMAIL=2,PERSON=1,...`, and logged
as one line per request carrying the session id (an opaque UUID) and the session's running totals per category,
so a long session can be confirmed as scrubbed.

## Stream restoration state

Per streamed response, per content block index:

| Block kind | Delta field | Restorer | Value insertion |
|---|---|---|---|
| `text` | `text_delta.text` | holdback restorer | raw value |
| `tool_use`, `server_tool_use`, other `input_json_delta` blocks | `input_json_delta.partial_json` | holdback restorer | value JSON-string-escaped (`json.dumps(v)[1:-1]`) |
| `thinking`, `redacted_thinking`, unknown | n/a | none | passed through byte-for-byte |

One restorer with a per-block escape function. Tokens can't occur outside a JSON string, because their
characters aren't valid JSON there, so tool input can stream through incrementally like text and is never held
back until it ends. Holding it back would starve Claude Code's stream idle timeout during long file writes.
