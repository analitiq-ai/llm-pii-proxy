# Feature Specification: OpenAI Privacy Filter replaces spaCy NER and the confidential pass

**Feature Branch**: `29-us1-privacy-filter`

**Created**: 2026-11-27

**Status**: Draft

**Input**: Issue #29. Code can reach Anthropic garbled, because spaCy NER labels code identifiers as names
(#28).

Decided with the user (2026-11-27):
- OpenAI Privacy Filter, used as published, replaces spaCy NER, the address recognizer and the email and
  phone patterns.
- The confidential pass on Privatemode is removed: Privacy Filter is the one context-aware detector.
  Privatemode stays for media conversion (specs/023-media-to-text) only.
- The proxy stays on the developer's machine (001 Assumptions): a machine with an NVIDIA GPU runs Privacy
  Filter on `cuda`, any other on `cpu`.
- A Privacy Filter span that ends inside a token is accepted as a known limitation (Edge Cases).

## Root cause

`en_core_web_lg` is a general-purpose English NER model, and `detection/presidio.py` runs it over every leaf,
code included. Its PERSON label fires on code: `\n").writerows(docs` in #28's reproduction,
`top.head`, `MAX_BATCH`, `app.main` in the measurements in [plan.md](plan.md). Presidio gives every spaCy
entity the same score, so `min_score` cannot separate these from real names. The known-value pass then
replaces each one again in every later leaf (#28 root cause 4). The defect is the detector, so the fix is the
detector, not rules layered over its output.

Issue #29's four causes, after this change:
1. Prose NER on code: removed with spaCy NER.
2. Spans that cross code punctuation: Privacy Filter returned `/jdoe` for the username in
   `/Users/jdoe/Documents` (plan.md § Measurements), so the path separators `/` and `\` join the delimiters
   trimmed from span edges (FR-006).
3. One false positive spreading through the session: not changed. The known-value pass stays, so a false
   positive from any detector still spreads; this change removes the detector that produced them on code.
   Whether to limit the spread is #28's decision.
4. Confidential-pass spans next to local tokens: removed with the confidential pass.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Code reaches Anthropic as written, PII is still replaced (Priority: P1)

A developer works through the proxy on code, logs and data files. Names, emails, phone numbers and
addresses in them are still replaced with placeholders, at the recall SC-002 requires. Code identifiers are
not taken for names or addresses, so Claude sees tool output and the files it reads back as they are on disk,
less the values the detectors replace.

**Why this priority**: garbled code makes Claude rewrite correct files (#28).

**Independent Test**: scrub #28's reproduction script with the configured detectors and compare it with the
input; run the recall benchmark.

**Acceptance Scenarios**:

1. **Given** #28's reproduction script in a tool result and its username `jdoe` on the always-scrub list,
   **When** the request is sent, **Then** the script reaches upstream byte-identical except that `jdoe` is
   replaced by its `PERSON` placeholder.
2. **Given** a file holding a person's name, an email address, a phone number and a postal address, **When**
   Claude reads it, **Then** each reaches upstream as a `PERSON`, `EMAIL`, `PHONE` or `ADDRESS` placeholder
   and is restored in the response.
3. **Given** a payment card number, an IBAN, a US SSN, a UK NHS number and an IP address, **When** the
   request is sent, **Then** each is replaced, and a card, IBAN, SSN or NHS number failing its checksum or
   validity rule is not.
4. **Given** a detected secret whose value ends in `/`, `,`, `;` or `|`, **When** its leaf is scrubbed,
   **Then** it is replaced whole, that character included.
5. **Given** the Privacy Filter checkpoint is missing, unreadable, or of a label set other than v2, or
   `cuda` is configured and no CUDA device is usable, **When** the proxy starts, **Then** it exits with an
   error naming the cause before it opens its port.
6. **Given** Privacy Filter raises while detecting, or returns a label outside its v2 set, **When** a request
   is sent, **Then** the request is blocked and nothing is forwarded (001 FR-007).
7. **Given** `/Users/jdoe/Documents` and `C:\Users\jdoe\AppData`, with `jdoe` detected, **When** the request
   is sent, **Then** only `jdoe` is replaced, and both separators around it reach upstream.

### Edge Cases

- Privacy Filter's v2 labels `account_number`, `private_url`, `private_date` and `secret` map to no
  category, and their spans are dropped. Cards, IBANs and secrets keep their own detectors; dates and URLs
  are not 001 FR-003 categories, and a date label would replace every log timestamp.
- Privacy Filter can end a span inside a token: in `ssh mgarcia@build-host` it returned `mgarcia@build`
  (plan.md § Measurements). The span is replaced as returned, so Anthropic sees `PII_EMAIL_0002-host`
  there, and restoration gives back `mgarcia@build-host`. Accepted as a known limitation; no rule is
  layered over the span.
- An IP literal in code, such as `0.0.0.0` in a Dockerfile, is still replaced as `IP` (001 FR-003, FR-002).
- A leaf longer than Privacy Filter's context window is split into windows by Privacy Filter itself.
- A Privacy Filter span that overlaps a pattern or gitleaks span is resolved like any two overlapping spans
  (001 data-model.md § DetectedSpan).
- On `cpu`, detection is slow (plan.md § Measurements), and SC-004 does not hold there.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Personal names, email addresses, phone numbers and postal addresses (001 FR-003) MUST be
  detected by OpenAI Privacy Filter at the pinned code commit and model revision (plan.md), unmodified, with
  its default decoding. Its v2 labels MUST map `private_person` to `PERSON`, `private_address` to
  `ADDRESS`, `private_email` to `EMAIL` and `private_phone` to `PHONE`, and spans labelled
  `account_number`, `private_url`, `private_date` or `secret` MUST be dropped. A span with any other label
  MUST fail the request (001 FR-007).
- **FR-002**: spaCy NER (`en_core_web_lg` and Presidio's NLP engine), the address recognizer, Presidio's
  email and phone recognizers and the NER chunker MUST be removed. Card, IBAN, national ID and IP numbers
  MUST stay detected by Presidio's pattern recognizers, checksums included, run without an NLP engine, and
  results scored below `PII_PROXY_MIN_SCORE` MUST be dropped. Secrets MUST stay detected by gitleaks.
- **FR-003**: The confidential pass MUST be removed: its detector, the `CONTEXT` category, a span's detection
  stage, its settings and its scrub-report count. Privatemode MUST remain only as the media conversion
  service, configured by `PII_PROXY_PRIVATEMODE_URL` and `PII_PROXY_PRIVATEMODE_TIMEOUT_S`.
- **FR-004**: `PII_PROXY_PRIVACY_FILTER_DEVICE` (`cpu` or `cuda`, default `cpu`) MUST select where Privacy
  Filter runs, and `PII_PROXY_PRIVACY_FILTER_PATH` where its checkpoint is read from. On `cpu`, Privacy
  Filter MUST use every CPU the process may run on.
- **FR-005**: The proxy MUST load Privacy Filter, check that its checkpoint's label set is v2, and complete
  one detection before it opens its port. Any failure MUST exit the proxy with an error naming the cause.
- **FR-006**: Edge trimming (001 data-model.md § DetectedSpan) MUST also trim `/` and `\` from both edges of
  every detected span. Secret spans MUST lose only whitespace and line breaks to trimming, never a delimiter
  character.

## Success Criteria *(mandatory)*

- **SC-001**: #28's reproduction script, scrubbed with the configured detectors and a vault holding only
  `jdoe` from the always-scrub list, comes back byte-identical except that `jdoe` is replaced.
- **SC-002**: The benchmark corpus meets 001 SC-003's recall targets.
- **SC-003**: The code set, the 19 code samples of plan.md § Measurements that hold no name, email, phone
  number or address, committed as a fixture and scrubbed with the configured detectors, gets no `PERSON`,
  `ADDRESS`, `EMAIL` or `PHONE` placeholder.
- **SC-004**: On `cuda`, 001 SC-004 holds: under 0.5 s added before the first streamed byte for 20 KB of new
  content. Measured on a GPU machine; none is available while this story is built.

## Assumptions

- The developer's machine runs the proxy (001 Assumptions). A remote or shared GPU host is out of scope.
- PyTorch publishes no wheels for macOS on x86-64, so the proxy runs on Linux (x86-64, ARM) and macOS on
  Apple silicon. Apple silicon runs Privacy Filter on `cpu`: Privacy Filter supports `cpu` and `cuda` only.
- The checkpoint takes 2.8 GB on disk. Privacy Filter peaked at 4.3 GB of memory on `cpu` (plan.md).
- Privacy Filter is a model: a name it was not trained on can be missed, as spaCy misses some. SC-002 is the
  bound.
