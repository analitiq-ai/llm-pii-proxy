"""Images and PDFs become text on Privatemode before the scrub (specs/023-media-to-text/plan.md).

The text holds the real values the image or PDF did; the scrub that follows replaces them.
"""

import asyncio
import base64
import binascii
import functools
import hashlib
import json
import logging
import shutil
import tempfile
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from llm_pii_proxy.errors import (
    BadRequest,
    MediaRenderTimedOut,
    PrivatemodeInvalid,
    ProxyError,
    as_proxy_error,
)
from llm_pii_proxy.privatemode import complete
from llm_pii_proxy.processes import kill_process
from llm_pii_proxy.walker import apply_binary_blocks, collect_binary_blocks

PDFTOPPM = "pdftoppm"
PDF = "application/pdf"
IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
OCR_PROMPT = "Convert the document to markdown."
VISION_PROMPT = (
    "Describe this image in full: everything it shows, every piece of text in it, and the data of "
    "any chart or table. Set document to true if it is a page of text, a form, a table or a "
    "scanned document, and false for a photo, a diagram or a chart."
)
_VISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["document", "description"],
    "properties": {"document": {"type": "boolean"}, "description": {"type": "string"}},
}
_PURPOSE = "media conversion"
_DISABLED = (
    "images and PDFs are not forwarded; set PII_PROXY_MEDIA_ENABLED=true to convert them "
    "to text on Privatemode"
)
log = logging.getLogger("llm_pii_proxy")


class PdfRendererMissing(RuntimeError):
    pass


def require_pdf_renderer() -> None:
    if shutil.which(PDFTOPPM) is None:
        raise PdfRendererMissing(
            f"{PDFTOPPM} not found on PATH; install poppler-utils or unset PII_PROXY_MEDIA_ENABLED"
        )


class _VisionAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    document: bool
    description: str


@dataclass(frozen=True, slots=True)
class _Converted:
    """A PDF's page Markdown in page order, or an image's description and, for a document, its
    Markdown."""

    parts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Media:
    """One block's decoded content; `key` identifies it in the cache."""

    kind: str
    media_type: str
    data: bytes
    key: str


@dataclass(slots=True)
class _InFlight:
    """A conversion still running, and how many requests are waiting on it."""

    task: "asyncio.Task[_Converted]"
    waiters: int = 0


async def convert_media(
    body: dict[str, Any], converter: "MediaConverter | None"
) -> tuple[dict[str, Any], int]:
    """`body` with every image and PDF block replaced by text, and how many were replaced.
    `converter` is None when conversion is off: any such block is then refused."""
    blocks = collect_binary_blocks(body)
    if not blocks:
        return body, 0
    media = [_validate(block, enabled=converter is not None) for block in blocks]
    converted = await converter.convert(media)
    replacements = [_text_block(b, c) for b, c in zip(blocks, converted, strict=True)]
    return apply_binary_blocks(body, replacements), len(blocks)


class MediaConverter:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        ocr_model: str,
        vision_model: str,
        concurrency: int,
        cache_entries: int,
        render_timeout_s: float,
    ) -> None:
        self._client = client
        self._ocr_model = ocr_model
        self._vision_model = vision_model
        self._slots = asyncio.Semaphore(concurrency)
        self._cache: OrderedDict[str, _Converted] = OrderedDict()
        self._cache_entries = cache_entries
        self._render_timeout_s = render_timeout_s
        # One per conversion still running, shared by every request holding the same block.
        self._running: dict[str, _InFlight] = {}

    async def convert(self, media: Sequence[_Media]) -> list[_Converted]:
        unique = {m.key: m for m in media}
        results = await asyncio.gather(
            *(self._converted(m) for m in unique.values()), return_exceptions=True
        )
        failures = [r for r in results if isinstance(r, BaseException)]
        if failures:
            first = next((f for f in failures if isinstance(f, ProxyError)), failures[0])
            raise as_proxy_error(first, _PURPOSE)
        by_key = {k: r for k, r in zip(unique, results, strict=True) if isinstance(r, _Converted)}
        return [by_key[m.key] for m in media]

    async def _converted(self, media: _Media) -> _Converted:
        hit = self._cache.get(media.key)
        if hit is not None:
            self._cache.move_to_end(media.key)
            return hit
        running = self._running.get(media.key)
        if running is None:
            running = _InFlight(asyncio.create_task(self._convert(media)))
            self._running[media.key] = running
            running.task.add_done_callback(functools.partial(self._finished, media.key, running))
        running.waiters += 1
        try:
            # Shielded: one request going away must not cancel a conversion others are waiting on.
            return await asyncio.shield(running.task)
        finally:
            running.waiters -= 1

    def _finished(self, key: str, running: _InFlight, task: "asyncio.Task[_Converted]") -> None:
        # Runs before any waiter resumes, so `waiters` counts only requests still to receive it.
        del self._running[key]
        if task.cancelled():
            return
        failure = task.exception()
        if failure is not None:
            if not running.waiters:
                # shield() would otherwise drop it silently. The type only: its message may quote
                # the block's content.
                log.error("%s failed with no request left: %s", _PURPOSE, type(failure).__name__)
            return
        self._cache[key] = task.result()
        while len(self._cache) > self._cache_entries:
            self._cache.popitem(last=False)

    async def _convert(self, media: _Media) -> _Converted:
        if media.kind == "document":
            pages = await render_pdf(media.data, self._render_timeout_s)
            markdown = await asyncio.gather(*(self._ocr("image/png", page) for page in pages))
            return _Converted(tuple(markdown))
        answer = await self._describe(media)
        if not answer.document:
            return _Converted((answer.description,))
        return _Converted((answer.description, await self._ocr(media.media_type, media.data)))

    async def _ocr(self, media_type: str, data: bytes) -> str:
        request = {
            "model": self._ocr_model,
            "messages": [
                {"role": "user", "content": [_image_part(media_type, data), _text_part(OCR_PROMPT)]}
            ],
            "temperature": 0,
        }
        return await self._complete(request)

    async def _describe(self, media: _Media) -> _VisionAnswer:
        request = {
            "model": self._vision_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        _image_part(media.media_type, media.data),
                        _text_part(VISION_PROMPT),
                    ],
                }
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "image_description",
                    "strict": True,
                    "schema": _VISION_SCHEMA,
                },
            },
            "temperature": 0,
        }
        content = await self._complete(request)
        try:
            return _VisionAnswer.model_validate(json.loads(content))
        except (ValueError, ValidationError):
            raise PrivatemodeInvalid(f"{_PURPOSE} returned an invalid answer") from None

    async def _complete(self, request: dict[str, Any]) -> str:
        async with self._slots:
            content = await complete(self._client, request, _PURPOSE)
        if isinstance(content, ProxyError):
            raise content
        return content


async def render_pdf(data: bytes, timeout_s: float) -> list[bytes]:
    """Each page of the PDF `data` as a PNG, in page order. The PDF is passed on stdin; only the
    page images touch disk, in a private temporary directory removed on return."""
    with tempfile.TemporaryDirectory(prefix="pii-proxy-pdf-") as directory:
        process = await asyncio.create_subprocess_exec(
            PDFTOPPM,
            "-r",
            "150",
            "-png",
            "-",
            str(Path(directory) / "page"),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(process.communicate(data), timeout_s)
        except TimeoutError:
            raise MediaRenderTimedOut("PDF rendering timed out") from None
        finally:
            # Also on cancellation: the directory is removed next, so the renderer must be gone.
            await kill_process(process)
        # pdftoppm zero-pads page numbers to one width, so name order is page order.
        pages = sorted(Path(directory).glob("page-*.png"))
        if process.returncode != 0 or not pages:
            raise BadRequest("PDF could not be rendered")
        return [page.read_bytes() for page in pages]


def _validate(block: dict[str, Any], *, enabled: bool) -> _Media:
    """The block's decoded content, or the 400 naming the first thing wrong with it, in the row
    order of plan.md § Conversion."""
    kind = block.get("type")
    if kind not in ("image", "document"):
        raise BadRequest(f"{kind} blocks with a binary source are not supported")
    source = block["source"]
    if source["type"] != "base64":
        raise BadRequest("image and PDF blocks given by URL or file id are not supported")
    if not enabled:
        raise BadRequest(_DISABLED)
    media_type = source.get("media_type")
    if not isinstance(media_type, str):
        raise BadRequest(f"{kind} block media type is missing or not a string")
    encoded = source.get("data")
    if not isinstance(encoded, str) or not encoded:
        raise BadRequest(f"{kind} block data is missing, empty or not a string")
    try:
        data = base64.b64decode(encoded, validate=True)
    except binascii.Error:
        raise BadRequest(f"{kind} block data is not valid base64") from None
    if media_type not in (IMAGE_TYPES if kind == "image" else {PDF}):
        raise BadRequest(f"{kind} media type {media_type} is not supported")
    key = hashlib.sha256(media_type.encode() + b"\0" + data).hexdigest()
    return _Media(kind, media_type, data, key)


def _text_block(block: dict[str, Any], converted: "_Converted") -> dict[str, Any]:
    if block["type"] == "document":
        # The title is not part of the cache key, so it is added here rather than cached.
        n = len(converted.parts)
        title = block.get("title")
        name = f'PDF "{title}"' if isinstance(title, str) and title else "PDF"
        text = (
            f"[{name}, {n} page{'' if n == 1 else 's'}, converted to text by pii-proxy]"
            + "".join(
                f"\n\n--- Page {i} ---\n\n{page}" for i, page in enumerate(converted.parts, 1)
            )
        )
    else:
        text = "\n\n".join(("[Image, converted to text by pii-proxy]", *converted.parts))
    out: dict[str, Any] = {"type": "text", "text": text}
    if "cache_control" in block:
        out["cache_control"] = block["cache_control"]
    return out


def _image_part(media_type: str, data: bytes) -> dict[str, Any]:
    url = f"data:{media_type};base64,{base64.b64encode(data).decode()}"
    return {"type": "image_url", "image_url": {"url": url}}


def _text_part(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}
