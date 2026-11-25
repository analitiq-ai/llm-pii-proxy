# Implementation Plan: Images and PDFs become scrubbed text

**Spec**: [spec.md](spec.md) · **Issue**: #23

## Summary

A new step in `App._scrub`, before `collect_leaves`: every `image` and `document` block with a binary
source under `messages` is replaced by a `text` block built on Privatemode. The scrub then runs unchanged
over the new text leaves. The walker stops skipping binary sources: one reaching it is a bug and raises.

## Settings (`PII_PROXY_` prefix)

| Field | Default | Rule |
|---|---|---|
| `media_enabled` | `false` | when false, a base64 image or PDF is refused (FR-001) |
| `media_ocr_model` | `deepseek-ocr-2` | |
| `media_vision_model` | `glm-5.3-flash` | |
| `media_concurrency` | `4` | ≥ 1; Privatemode calls in flight per process |
| `media_cache_entries` | `256` | ≥ 1; least recently used entry evicted first |

Privatemode is reached at `confidential_url` with `confidential_timeout_s`, as the confidential pass is: one
`httpx.AsyncClient`, built when either feature is enabled. With `media_enabled`, startup fails if
`pdftoppm` is not on `PATH`.

## Conversion (`src/llm_pii_proxy/media.py`)

Which blocks: every dict holding a `source` whose `type` is `base64`, `url` or `file`, wherever the walker
already finds them (`system` and `messages`, thinking blocks untouched). There is one traversal:
`walker.py` gains `collect_binary_blocks(body)` and `apply_binary_blocks(body, replacements)`, the
counterparts of `collect_leaves` and `apply_leaves`, built on the same `_walk` rules; `media.py` calls
them and does not walk the body itself. A block of a type other than `image` or `document` holding such
a source is refused with 400 naming its type.

| Block | Outcome |
|---|---|
| source `url` or `file` | `BadRequest` 400: "image and PDF blocks given by URL or file id are not supported" |
| media disabled | `BadRequest` 400 naming `PII_PROXY_MEDIA_ENABLED` |
| `image`, media type `image/png`, `image/jpeg`, `image/gif`, `image/webp` | vision call; OCR call when `document` |
| `document`, media type `application/pdf` | `pdftoppm -r 150 -png` into a temp dir (deleted after), one OCR call per page |
| any other media type | `BadRequest` 400 naming the media type |
| `pdftoppm` exits non-zero, or runs past `confidential_timeout_s` (then killed) | `BadRequest` 400 "PDF could not be rendered" |

Privatemode calls, `POST /v1/chat/completions`, `temperature: 0`, image as a `data:` URL `image_url` part
(both checked against Privatemode on 2026-11-25):

- OCR: model `media_ocr_model`, user content `[image, "Convert the document to markdown."]`. The
  `<|grounding|>` prompt is not used: it adds layout tags to the text.
- Vision: model `media_vision_model`, prompt asking for a description and whether the image is a page of
  text, a form, a table or a scanned document, with `response_format` a strict JSON schema
  `{document: boolean, description: string}`, validated with a strict Pydantic model.

Both these calls and the confidential pass's go through one helper, `privatemode.complete(client, request,
purpose)`, moved out of `ConfidentialDetector._ask`: timeout, transport error or non-2xx →
`ConfidentialUnavailable` (503); an answer without a string `content` → `ConfidentialInvalid` (502). A
vision answer that does not match the schema → `ConfidentialInvalid`. Causes name the purpose and failure
kind only.

Replacement block: `{"type": "text", "text": ..., "cache_control"?: ...}`, keeping the original's
`cache_control` if present. Text:

- image: `[Image, converted to text by pii-proxy]\n\n<description>` plus `\n\n<markdown>` for a document;
- PDF: `[PDF "<title>", N pages, converted to text by pii-proxy]` (`title` omitted when absent, `page`
  when N is 1), then per page `\n\n--- Page i ---\n\n<markdown>`.

All blocks of a request convert concurrently, bounded by one `asyncio.Semaphore(media_concurrency)`.

Cache: an `OrderedDict` LRU, key `sha256(media_type + "\0" + decoded bytes)`, value the converted parts
before scrubbing (a PDF's page Markdown; an image's description and Markdown). The replacement text is
built from them per block, because a PDF's `title` is not in the key. Identical blocks in one request
convert once, and a conversion still running is shared by every request holding the same block (one
task per key, shielded so one request going away does not cancel it). Only successful conversions are
stored. Process-wide, in memory only (FR-005).

## Other changes

- `walker.py`: a binary source raises `ValueError`; `Leaves.skipped_binary` and the report's
  `skipped_binary` are removed. `ScrubReport.media_converted` counts replaced blocks, shown in the header
  and log line as `media_converted=<n>`.
- `specs/001-pii-scrubbing-proxy/contracts/http-interface.md`: the binary-source row now points here
  (changed in this spec PR).
- README: § Features gains the conversion; settings table gains the five fields; `pdftoppm` under
  requirements.

## Testing

Privatemode is mocked with `respx`, as for the confidential pass. Test PDFs are written in the test as
minimal PDF bytes (one text object per page, no new dependency) and rendered by the real `pdftoppm`. A manual check,
`scripts/e2e/media.py`, sends one real PDF and one image through a running proxy against real Privatemode
and Anthropic and asserts SC-001 on the capture.
