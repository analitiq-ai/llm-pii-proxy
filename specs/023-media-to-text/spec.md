# Feature Specification: Images and PDFs become scrubbed text

**Feature Branch**: `23-us1-media-to-text`

**Created**: 2026-11-25

**Status**: Draft

**Input**: Issue #23. Images and PDFs must not be sent to Anthropic. They are converted to text inside
Privatemode's confidential environment, scrubbed like all other text, and sent as text.

Decided with the user (2026-11-25): one OCR model for PDF pages (no Docling, no separate chart model);
image and PDF blocks given by URL or file id are refused; an image is described by a vision model, and
read by the OCR model too when it is a document.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Share an image or PDF without its bytes leaving the machine (Priority: P1)

A developer gives Claude Code a screenshot or a PDF, by pasting it or by having Claude read the file. The
proxy replaces each image and PDF with text: what the image shows, the text of each page, tables as
Markdown. The text is produced on Privatemode, then scrubbed exactly like the rest of the request. Claude
works from that text. Anthropic never receives image or PDF bytes.

**Why this priority**: today images and PDFs pass through unscrubbed; this closes that leak.

**Independent Test**: send a request holding a base64 PNG of an invoice with a name and address on it and a
two-page PDF holding the same data, with Privatemode mocked. The upstream request holds no `image` or
`document` block with a binary source, holds the converted text in their place, and the name and address
appear only as placeholders.

**Acceptance Scenarios**:

1. **Given** a user message with a base64 PDF, **When** it is sent, **Then** the PDF block is replaced, in the
   same position, by one text block holding each page's Markdown in page order, and that text is scrubbed.
2. **Given** a base64 image that the vision model calls a document, **When** it is sent, **Then** it is replaced
   by a text block holding the model's description and the OCR model's Markdown of it.
3. **Given** a base64 image that the vision model does not call a document (a photo, a diagram, a chart),
   **When** it is sent, **Then** it is replaced by a text block holding the description only.
4. **Given** an image or PDF inside a `tool_result`, **When** it is sent, **Then** it is replaced there too.
5. **Given** the same image or PDF in a later request of the conversation, while its conversion is still
   cached, **When** it is sent, **Then** it is not converted again, and the text sent in its place is
   identical to the first time.
6. **Given** an image or PDF whose source is a URL or a file id, **When** it is sent, **Then** the request is
   refused with 400 and nothing is forwarded.
7. **Given** Privatemode is unreachable, times out or answers an error, **When** a request holds an image or
   PDF, **Then** the request fails with 503 and nothing is forwarded.
8. **Given** conversion is not enabled, **When** a request holds a base64 image or PDF, **Then** it is refused
   with 400 naming the setting that enables conversion.

### Edge Cases

- A PDF that cannot be rendered (corrupt, password-protected): 400; the cause names the failure kind only.
- A PDF whose rendering runs past the Privatemode timeout: 503, so the client may retry.
- An image or document media type the proxy does not convert: 400 naming the media type.
- A `cache_control` on the replaced block stays on the text block that replaces it, so the client's prompt
  cache breakpoints still apply.
- `count_tokens` requests are converted the same way, so the count matches what `messages` sends.
- A document whose source is `text` is scrubbed as today. A document whose source is `content` is walked
  like any other block list: its text is scrubbed, and an image nested in it is converted like any other.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: No `image` or `document` block with a `base64`, `url` or `file` source is ever forwarded
  upstream, whatever the settings.
- **FR-002**: A base64 PDF is rendered to page images locally, and each page is converted to Markdown by the
  OCR model on Privatemode.
- **FR-003**: A base64 image is described by the vision model on Privatemode, which also says whether it is a
  document; a document image is also converted to Markdown by the OCR model.
- **FR-004**: The converted text replaces the block in place, wherever under `messages` it is, and is then
  scrubbed as any other text leaf, including by the confidential pass when that is enabled.
- **FR-005**: Converted text is cached in memory by content hash, up to a configured number of entries. A
  block repeated while its entry is cached is neither converted again nor sent with different text; once
  evicted, it converts again and its text may differ. The cache holds real values: it is never logged or
  written to disk.
- **FR-006**: Any conversion failure rejects the request; error causes never quote content.
- **FR-007**: The scrub report counts converted blocks; it carries no content.

## Success Criteria *(mandatory)*

- **SC-001**: In the e2e run, no captured upstream request holds an image or PDF block with a binary source.
- **SC-002**: A 10-page PDF converts in under 30 s with the default settings; repeating it costs no
  Privatemode call.

## Assumptions

- Privatemode serves `deepseek-ocr-2` and `glm-5.3-flash`, both accepting images (checked 2026-11-25).
- `pdftoppm` (poppler) is installed where the proxy runs.
- Claude works from the text only; questions that need the pixels themselves (exact colours, layout) get
  the description's answer, not the image's.
