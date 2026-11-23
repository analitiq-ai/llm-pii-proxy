# Feature Specification: PII-Scrubbing Proxy for Claude Code

**Feature Branch**: `001-pii-scrubbing-proxy`

**Created**: 2026-11-21

**Status**: Draft

**Input**: User description: "I want to set up a proxy for claude code and use privatemode (in proxy mode) to remove personaly identifyable information from prompts."

Clarified scope (user, 2026-11-21): Privatemode has no PII-removal feature of its own; it runs open-weights
models inside confidential computing. Scope is therefore two stages. First, a local scrubber: on-machine
detection of pattern-based and named-entity PII, swapped for reversible placeholders. Second, a confidential
second pass that sends the already-scrubbed text to a Privatemode-hosted model to catch context-dependent PII
the local pass missed. The fully scrubbed request then goes to Anthropic/Claude, and original values are
restored in responses. Claude remains the model doing the work.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Work in Claude Code without PII leaving the machine (Priority: P1)

A developer starts Claude Code through the proxy and works as usual on code, data files and logs that contain
personal data: names, emails, phone numbers, addresses, account numbers. Before any request leaves the
machine, every personal value the local detection recognises is replaced with a placeholder. The same value
always gets the same placeholder within the conversation (for example `PII_EMAIL_0001`). When Claude answers or acts
(writing a file, editing code, running a command), the proxy puts the original values back. Files on disk and
text on screen contain the real data. The model provider only ever sees placeholders.

**Why this priority**: This is the core promise. It is a usable product on its own, with no external
dependency beyond Anthropic.

**Independent Test**: Seed a project with files containing known PII values and run a set of scripted Claude
Code tasks through the proxy: read, summarise, edit, refactor. Capture all traffic leaving the machine and
confirm that none of the seeded values appear in it. Confirm that each task produces the same on-disk result
it would without the proxy.

**Acceptance Scenarios**:

1. **Given** a file containing `jane.doe@acme.com`, **When** the developer asks Claude to summarise it, **Then**
   the outbound request contains a placeholder instead of the address, and the summary shown to the developer
   contains `jane.doe@acme.com`.
2. **Given** a file containing a customer name and phone number, **When** the developer asks Claude to edit an
   unrelated line, **Then** the file on disk afterwards still contains the original name and phone number,
   unchanged.
3. **Given** a `.env` file containing an API key, **When** Claude reads it and changes another setting in it,
   **Then** the outbound request carries a placeholder instead of the key, and the file on disk afterwards
   still holds the real key.
4. **Given** the same email appears in three files and two turns, **When** the requests are sent, **Then** it
   maps to the same placeholder every time within that conversation.
5. **Given** a response streamed in pieces, where a placeholder is split across two pieces, **When** the
   developer watches it arrive, **Then** they never see a partial placeholder or a raw placeholder. They see
   only the restored value.
6. **Given** the local detection cannot process a request (for example, malformed content or an internal
   error), **When** the request is sent, **Then** it is blocked with a clear error and nothing is forwarded to
   the model provider.

---

### User Story 2 - Catch context-dependent PII with a confidential second pass (Priority: P2)

Some personal data has no recognisable pattern: a nickname, a job title that identifies one person, a rare
diagnosis next to a city, or a name the local model does not recognise. After the local pass, the proxy sends
the already-scrubbed text to a confidential detection service, a model in an attested environment that the
operator cannot read. The service flags remaining personal data. Flagged values are replaced with placeholders
the same way as in Story 1 before the request goes to the model provider, and are restored the same way in
responses.

**Why this priority**: It raises detection beyond what patterns and named-entity recognition can catch, but it
adds a dependency, latency and cost. Story 1 must already work without it.

**Independent Test**: Use a corpus of context-dependent PII examples that the local pass is known to miss. With
the second pass enabled, confirm the examples are replaced before leaving for the model provider. Confirm that
the detection service only ever received text the local pass had already scrubbed.

**Acceptance Scenarios**:

1. **Given** a paragraph "the only left-handed cardiologist at St. Mary's in Tralee" that the local pass leaves
   intact, **When** the request is sent with the second pass enabled, **Then** the identifying phrase is
   replaced with a placeholder before reaching the model provider and restored in the response.
2. **Given** text containing an email the local pass already replaced, **When** it is sent to the detection
   service, **Then** the service receives the placeholder, never the email.
3. **Given** a long conversation where earlier turns were already analysed, **When** a new turn is sent,
   **Then** only content not analysed before is sent to the detection service.
4. **Given** the detection service is unreachable or returns an unusable answer, **When** a request is sent,
   **Then** the proxy behaves as configured in FR-014.

---

### Edge Cases

- A placeholder-looking string already exists in the user's real data, for example literal text
  `PII_EMAIL_0001`. It must not be "restored" into someone's email.
- Claude invents a new placeholder that was never issued, such as `PII_EMAIL_0007` when only three exist. It is
  left as-is and flagged, never guessed.
- Claude reformats a placeholder, for example `pii_email_0001`. Case variants are restored; other mangling is
  left as-is.
- A detected value sits directly against other text, such as a short name inside a longer word or a value
  followed by digits. Neither the outbound request nor the restored file may be altered beyond the value
  itself.
- The developer's own identity (OS username in paths, git author name and email) appears in Claude Code's own
  instructions and in file paths on every request, where name detection is unreliable.
- PII appears inside structured data such as JSON, CSV, or code string literals. Replacement must not break the
  structure, so the file stays valid after restoration.
- The same person appears in different forms: "Jane Doe", "J. Doe", "jane.doe@…". Each distinct value gets its
  own placeholder. Linking variants is not required.
- Requests that carry no user content (connection checks, model listings) pass through unchanged. Token-count
  requests carry the full conversation and are scrubbed like model requests.
- Very large requests, such as a long history plus large files, must still be scrubbed in full. Size is never a
  reason to skip scrubbing.
- Detection false positives, for example a variable named `john` or a version number shaped like a phone
  number. Over-scrubbing is acceptable as long as restoration keeps files correct.
- PII inside images or other binary attachments.
- The proxy restarts mid-conversation. Placeholders are re-issued from the full history that Claude Code
  re-sends. Mismatched numbering across the restart costs efficiency but never correctness.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST sit between Claude Code and the model provider so that every model request from
  Claude Code passes through it. It MUST need nothing more than pointing Claude Code at the proxy's address.
- **FR-002**: The system MUST scan every part of an outbound request that can carry user content: system
  instructions, user messages, file contents and command output returned to the model, and earlier model
  actions replayed in history.
- **FR-003**: The system MUST detect at least these categories locally: personal names, email addresses, phone
  numbers, postal addresses, payment card numbers, bank account numbers (IBAN), national ID numbers, and IP
  addresses. It MUST also detect credentials and secrets (API keys, access tokens, passwords, private keys,
  connection strings with embedded credentials) and treat them exactly like PII: same placeholders, same
  restoration.
- **FR-004**: The system MUST replace each detected value with a placeholder that names its category and is
  stable for the life of the conversation: same value, same placeholder.
- **FR-005**: The system MUST restore original values in everything the model sends back: displayed text, and
  the inputs of actions Claude Code will carry out, such as file writes, edits and commands.
- **FR-006**: The system MUST restore values correctly when responses are delivered incrementally, including
  placeholders split across increments, without showing partial or raw placeholders to the user.
- **FR-007**: The system MUST fail closed. If any part of a request cannot be scrubbed, the request is blocked
  with an error naming the cause, and nothing is forwarded.
- **FR-008**: The system MUST keep the value-to-placeholder mapping only on the local machine and only in
  memory. It MUST never write it to logs, disk, or any outbound request.
- **FR-009**: The system MUST NOT log request or response content. Operational logs may hold only counts per
  category, timings and errors.
- **FR-010**: The system MUST leave unissued or unrecognised placeholder-like text in responses untouched,
  never guessing a value for it.
- **FR-011**: The system MUST distinguish its own placeholders from identical-looking text already in the
  user's data, so real data is never altered by restoration.
- **FR-012**: The system MUST, when the second pass is enabled, send to the confidential detection service only
  text the local pass has already scrubbed, and only content not analysed before in the same conversation.
- **FR-013**: The system MUST treat values flagged by the confidential detection service exactly like locally
  detected values: same placeholder scheme, same mapping, same restoration.
- **FR-014**: When the confidential detection service is enabled but fails (unreachable, times out, or returns
  an invalid answer), the system MUST block the request with an error naming the cause, the same fail-closed
  rule as FR-007. Continuing with local scrubbing only is done by disabling the second pass (FR-015), never
  silently.
- **FR-015**: The system MUST let the operator enable or disable the second pass without code changes.
- **FR-016**: The system MUST pass through, unchanged, requests that carry no user content.
- **FR-017**: The system MUST report, per request and without revealing values, how many items of each
  category were replaced, so the operator can see scrubbing is active.
- **FR-018**: The system MUST let the operator declare, in a local file that is never committed or sent
  anywhere, an **always-scrub** list (exact values always replaced, whether or not detection finds them, such
  as the developer's name, username, email and client names) and a **never-scrub** list (exact strings never
  replaced, such as project terms detection wrongly flags).
- **FR-019**: The system MUST tell the model, with the same fixed wording on every request, that placeholders
  stand in for real values that are restored on the way back, so it should use them verbatim wherever the real
  value is needed instead of refusing or asking for the value.

### Key Entities

- **Conversation**: one Claude Code session's sequence of requests. It is the scope of placeholder stability.
- **Detected value**: an original piece of personal data, with its category and the detection stage that found
  it (local or confidential).
- **Placeholder**: the stand-in that replaces a detected value outbound, named by category and sequence number,
  unique within a conversation.
- **Mapping**: the local, in-memory, two-way association between detected values and placeholders for one
  conversation.
- **Scrub report**: per-request counts of replaced items by category. It holds no values.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Across a test corpus of seeded PII and secret values in the FR-003 categories, 0 seeded values
  appear in traffic leaving the machine for the model provider.
- **SC-002**: A scripted set of at least 20 Claude Code tasks on PII-bearing files produces on-disk results
  identical to the same tasks run without the proxy, with 100% of original values preserved.
- **SC-003**: The local pass detects at least 95% of pattern-based PII (emails, phones, cards, IBANs, IPs) and
  at least 85% of personal names in the benchmark corpus, and at least 95% of secrets in common
  credential formats.
- **SC-004**: With only the local pass, users perceive no meaningful slowdown: the added delay before the first
  part of a response appears is under 0.5 seconds for typical requests. A typical request carries at most 20 KB
  of content the proxy hasn't seen before in the conversation.
- **SC-005**: With the second pass enabled, at least 70% of the context-dependent PII examples the local pass
  misses are replaced before reaching the model provider.
- **SC-006**: The confidential detection service receives 0 values that the local pass had already detected.
- **SC-007**: 100% of requests that fail scrubbing are blocked, with none forwarded, verified by fault-injection
  tests.
- **SC-008**: On a fixed set of at least 20 tasks over PII-bearing files, the success rate through the proxy is
  at least 95% of the success rate running directly. Each task has a pass check fixed before any run (expected
  file contents or answer). Each task runs 3 times per mode. The set includes tasks that must reuse a value
  (copy an email domain into a new row, report a value back) and excludes tasks that need to see a literal
  value (see Assumptions).

## Assumptions

- Single developer, local machine. The proxy listens only on the local machine and serves that developer's
  Claude Code. Shared or team deployment is out of scope.
- Pseudonymisation, not anonymisation: the local mapping can re-identify data by design. Its protection comes
  from never leaving the machine.
- Primary language of PII is English. Other languages are best-effort.
- PII inside images and binary attachments is out of scope for this feature. Such content is forwarded
  unchanged and noted in the scrub report.
- Restoration targets exact placeholders plus a small set of predictable reformatting variants (case, missing
  brackets). Arbitrary paraphrase by the model isn't restored.
- The mapping lives only in memory for the life of the proxy process. Losing it on restart is acceptable (see
  Edge Cases).
- Detection accuracy targets in SC-003 and SC-005 are initial targets. They will be calibrated once a benchmark
  corpus exists.
- Claude remains the working model. Replacing the provider (for example, running Claude Code on Privatemode's
  own models) is out of scope.
- Tool definitions sent by Claude Code (built-in and MCP tool descriptions) are forwarded unscrubbed. Rewriting
  them would break the model's integrity checks on earlier turns. Operator-specific text in MCP tool
  descriptions is a known gap.
- Tasks that need the model to *see* a literal value cannot succeed, for example "does this email look valid"
  or "fix the regex for these phone formats". Placeholders hide the value's content by design. Each placeholder
  replaces the whole value: no part of it, such as an email's domain, stays visible. A task that needs part of a
  value has Claude extract it with a tool from the real file, which costs an extra tool call.
- Build vs. adopt: the open-source PrivAiTe proxy was evaluated by running it (research.md R0). It passed the
  integrity-check test but failed FR-002 (instructions unscrubbed: it leaked the OS username), FR-004
  (inconsistent placeholders), and structure preservation. Building is the chosen path.

## Dependencies

- Claude Code supports routing its model traffic to a custom endpoint address.
- An Anthropic account or credentials for the model provider, which the proxy passes through and never stores.
- For Story 2: a Privatemode account, and the Privatemode proxy running locally to provide encrypted, attested
  access to its hosted open-weights models. Privatemode serves only as the detection service. It is never the
  model that does the coding work.
