"""SC-001 and SC-002 of specs/023-media-to-text. Manual: calls the real Privatemode proxy at
PII_PROXY_CONFIDENTIAL_URL, and Anthropic through a running pii-proxy.

    uv run python -m scripts.e2e.media convert
    uv run python -m scripts.e2e.media send --capture <dir>

`convert` (SC-002) converts a 10-page PDF in process with the default media settings, then again,
and exits 1 if the first took 30 s or more or the second made any Privatemode call.

`send` (SC-001) writes the PDF and an image of a page to a temporary directory and has Claude Code,
on your subscription, read both through the proxy at PROXY_URL (default http://127.0.0.1:8787),
while scripts/e2e/recorder.py records upstream to <dir>, which must start empty. It exits 1 if
Claude Code failed, either file never reached upstream as converted text, or any captured request
holds a block of any type with a binary source.
"""

import argparse
import asyncio
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import httpx

from llm_pii_proxy.config import Settings
from llm_pii_proxy.media import MediaConverter, convert_media, render_pdf
from tests.pdf import make_pdf

PAGES = [f"Page {i}: invoice INV-{1000 + i} for jane.doe@acme.example" for i in range(1, 11)]
LIMIT_S = 30.0
BINARY_SOURCES = ("base64", "url", "file")
READ_PROMPT = "Read invoices.pdf and page.png. How many invoices are there? Answer with a number."
# The headers media._text_block writes for the two files READ_PROMPT names.
CONVERTED_HEADERS = {
    "invoices.pdf": re.compile(rb"\[PDF, \d+ pages?, converted to text by pii-proxy\]"),
    "page.png": re.compile(rb"\[Image, converted to text by pii-proxy\]"),
}


def blocks(pdf: bytes, png: bytes) -> list[dict]:
    def source(media_type: str, data: bytes) -> dict:
        return {"type": "base64", "media_type": media_type, "data": base64.b64encode(data).decode()}

    return [
        {"type": "document", "source": source("application/pdf", pdf), "title": "Invoices"},
        {"type": "image", "source": source("image/png", png)},
        {"type": "text", "text": "How many invoices are there? Answer with a number."},
    ]


def body(content: list[dict]) -> dict:
    return {
        "model": "claude-haiku-5-5",
        "max_tokens": 32,
        "messages": [{"role": "user", "content": content}],
    }


async def convert() -> int:
    settings = Settings()
    calls = 0

    async def count(_: httpx.Request) -> None:
        nonlocal calls
        calls += 1

    pdf = make_pdf(PAGES)
    async with httpx.AsyncClient(
        base_url=settings.confidential_url,
        timeout=settings.confidential_timeout_s,
        event_hooks={"request": [count]},
    ) as client:
        converter = MediaConverter(
            client,
            ocr_model=settings.media_ocr_model,
            vision_model=settings.media_vision_model,
            concurrency=settings.media_concurrency,
            cache_entries=settings.media_cache_entries,
            render_timeout_s=settings.confidential_timeout_s,
        )
        request = body(blocks(pdf, b"")[:1])
        start = time.monotonic()
        await convert_media(request, converter)
        took = time.monotonic() - start
        first_calls = calls
        await convert_media(request, converter)
    repeat_calls = calls - first_calls
    print(f"first: {took:.1f}s, {first_calls} Privatemode calls; repeat: {repeat_calls} calls")
    return 0 if took < LIMIT_S and repeat_calls == 0 else 1


def binary_blocks(node: object) -> Iterator[str]:
    """The type of each block in `node`, of any type, with a binary source."""
    if isinstance(node, dict):
        source = node.get("source")
        if isinstance(source, dict) and source.get("type") in BINARY_SOURCES:
            yield str(node.get("type"))
        for value in node.values():
            yield from binary_blocks(value)
    elif isinstance(node, list):
        for value in node:
            yield from binary_blocks(value)


async def send(capture: Path) -> int:
    if any(capture.iterdir()):
        sys.exit(f"{capture} is not empty; give send a fresh capture directory")
    png = (await render_pdf(make_pdf(PAGES[:1]), Settings().confidential_timeout_s))[0]
    env = os.environ | {"ANTHROPIC_BASE_URL": os.environ.get("PROXY_URL", "http://127.0.0.1:8787")}
    with tempfile.TemporaryDirectory(prefix="pii-proxy-e2e-media-") as work:
        Path(work, "invoices.pdf").write_bytes(make_pdf(PAGES))
        Path(work, "page.png").write_bytes(png)
        run = subprocess.run(
            ["claude", "-p", "--allowedTools", "Read", "--output-format", "json", READ_PROMPT],
            cwd=work,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    print(f"claude exited {run.returncode}")
    requests = [(req.name, req.read_bytes()) for req in sorted(capture.glob("*.req"))]
    leaked = [
        (name, kind) for name, raw in requests for kind in binary_blocks(json.loads(raw or b"{}"))
    ]
    for name, kind in leaked:
        print(f"{name}: {kind} block with a binary source")
    missing = [
        file
        for file, header in CONVERTED_HEADERS.items()
        if not any(header.search(raw) for _, raw in requests)
    ]
    for file in missing:
        print(f"{file}: never reached upstream as converted text")
    return 0 if run.returncode == 0 and not missing and not leaked else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("convert")
    commands.add_parser("send").add_argument("--capture", type=Path, required=True)
    args = parser.parse_args()
    sys.exit(asyncio.run(convert() if args.command == "convert" else send(args.capture)))


if __name__ == "__main__":
    main()
