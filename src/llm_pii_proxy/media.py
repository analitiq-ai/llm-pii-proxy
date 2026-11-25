"""Images and PDFs become text on Privatemode before the scrub (specs/023-media-to-text/plan.md).

The text holds the real values the image or PDF did; the scrub that follows replaces them.
"""

import asyncio
import base64
import binascii
import hashlib
import json
import shutil
import tempfile
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from llm_pii_proxy.errors import BadRequest, ConfidentialInvalid, ProxyError
from llm_pii_proxy.privatemode import complete
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


async def convert_media(
    body: dict[str, Any], converter: "MediaConverter | None"
) -> tuple[dict[str, Any], int]:
    """`body` with every image and PDF block replaced by text, and how many were replaced.
    `converter` is None when conversion is off: any such block is then refused."""
    blocks = collect_binary_blocks(body)
    if not blocks:
        return body, 0
    if any(block["source"]["type"] != "base64" for block in blocks):
        raise BadRequest("image and PDF blocks given by URL or file id are not supported")
    if converter is None:
        raise BadRequest(
            "images and PDFs are not forwarded; set PII_PROXY_MEDIA_ENABLED=true to convert them "
            "to text on Privatemode"
        )
    replacements = await converter.convert(blocks)
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
    ) -> None:
        self._client = client
        self._ocr_model = ocr_model
        self._vision_model = vision_model
        self._slots = asyncio.Semaphore(concurrency)
        self._cache: OrderedDict[str, _Converted] = OrderedDict()
        self._cache_entries = cache_entries

    async def convert(self, blocks: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """A text block for each of `blocks`, all base64 sources."""
        media = [_decode(block) for block in blocks]
        found = {m.key: hit for m in media if (hit := self._cached(m.key)) is not None}
        # A block repeated within the request converts once.
        missing = {m.key: m for m in media if m.key not in found}
        try:
            async with asyncio.TaskGroup() as group:
                tasks = {key: group.create_task(self._convert(m)) for key, m in missing.items()}
        except* ProxyError as failures:
            raise failures.exceptions[0] from None
        for key, task in tasks.items():
            found[key] = task.result()
            self._store(key, found[key])
        return [_text_block(block, found[m.key]) for block, m in zip(blocks, media, strict=True)]

    def _cached(self, key: str) -> "_Converted | None":
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
        return hit

    def _store(self, key: str, converted: "_Converted") -> None:
        self._cache[key] = converted
        while len(self._cache) > self._cache_entries:
            self._cache.popitem(last=False)

    async def _convert(self, media: _Media) -> "_Converted":
        if media.kind == "document":
            pages = await render_pdf(media.data)
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
            raise ConfidentialInvalid(f"{_PURPOSE} returned an invalid answer") from None

    async def _complete(self, request: dict[str, Any]) -> str:
        async with self._slots:
            content = await complete(self._client, request, _PURPOSE)
        if isinstance(content, ProxyError):
            raise content
        return content


async def render_pdf(data: bytes) -> list[bytes]:
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
        await process.communicate(data)
        # pdftoppm zero-pads page numbers to one width, so name order is page order.
        pages = sorted(Path(directory).glob("page-*.png"))
        if process.returncode != 0 or not pages:
            raise BadRequest("PDF could not be rendered")
        return [page.read_bytes() for page in pages]


def _decode(block: dict[str, Any]) -> _Media:
    kind = block.get("type")
    media_type = block["source"].get("media_type")
    if kind == "image":
        if media_type not in IMAGE_TYPES:
            raise BadRequest(f"image media type {media_type} is not supported")
    elif kind == "document":
        if media_type != PDF:
            raise BadRequest(f"document media type {media_type} is not supported")
    else:
        raise BadRequest(f"{kind} blocks with a binary source are not supported")
    try:
        data = base64.b64decode(block["source"].get("data", ""), validate=True)
    except (binascii.Error, ValueError):
        raise BadRequest(f"{kind} block data is not valid base64") from None
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
