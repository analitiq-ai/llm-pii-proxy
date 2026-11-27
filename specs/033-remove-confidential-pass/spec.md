# Feature Specification: Remove the confidential pass

**Feature Branch**: `33-us1-remove-confidential-pass`

**Created**: 2026-11-27

**Status**: Draft

**Input**: Issue #33. The confidential pass, 001 User Story 2, is not needed.

Decided with the user (2026-11-27):
- The confidential pass is removed. Local detection is unchanged.
- Privatemode stays as the media conversion service only ([specs/023-media-to-text](../023-media-to-text/spec.md)).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The proxy scrubs with local detection only (Priority: P1)

The operator runs the proxy as before. No setting turns on a second detection pass, and the proxy talks to
Privatemode only to convert images and PDFs.

**Why this priority**: it is the whole change.

**Independent Test**: run the test suite.

**Acceptance Scenarios**:

1. **Given** media conversion disabled, **When** a request is sent, **Then** the proxy sends nothing to
   Privatemode.
2. **Given** media conversion enabled, **When** a request holds no image or PDF, **Then** the proxy sends
   nothing to Privatemode.
3. **Given** media conversion enabled and `PII_PROXY_PRIVATEMODE_URL` set, **When** a request holds an
   image, **Then** the image is converted through that URL.

### Edge Cases

- A `PII_PROXY_CONFIDENTIAL_*` variable left in the environment has no effect, like any `PII_PROXY_` name
  the proxy does not define. Media conversion reads only the names FR-002 gives.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The confidential pass MUST be removed: its detector, the `CONTEXT` category, a span's detection
  stage, its settings, its recall script and fixture, and the scrub report's stage key and unlocated count.
- **FR-002**: Media conversion MUST read Privatemode's base URL from `PII_PROXY_PRIVATEMODE_URL` and its
  timeout from `PII_PROXY_PRIVATEMODE_TIMEOUT_S`, with the defaults and checks of the
  `PII_PROXY_CONFIDENTIAL_URL` and `PII_PROXY_CONFIDENTIAL_TIMEOUT_S` they replace.
- **FR-003**: The proxy MUST create its Privatemode client only when media conversion is enabled.

## Success Criteria *(mandatory)*

- **SC-001**: The test suite passes, and no code, test or script left in the repository uses the confidential
  pass (plan.md § Files the story changes).
- **SC-002**: The scrub report header, `x-pii-proxy-scrubbed`, keeps its format: counts by category, then
  `media_converted`.

## Assumptions

- Upgrading restarts the proxy, which loses every session's placeholders, `CONTEXT` ones included (001 Edge
  Cases).
