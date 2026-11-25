"""Image and PDF conversion (specs/023-media-to-text/plan.md § Conversion). Privatemode is mocked;
PDFs are rendered by the real `pdftoppm`."""

import asyncio
import base64
import copy
import json
import os

import httpx
import pytest
import respx

from llm_pii_proxy import media as media_module
from llm_pii_proxy.errors import (
    BadRequest,
    PrivatemodeInvalid,
    PrivatemodeUnavailable,
    ProxyError,
)
from llm_pii_proxy.media import MediaConverter, convert_media, render_pdf
from tests.pdf import make_pdf

PRIVATEMODE = "http://privatemode.test"
OCR_MODEL = "ocr-model"
VISION_MODEL = "vision-model"
PNG = base64.b64encode(b"\x89PNG not really an image").decode()
OTHER_PNG = base64.b64encode(b"\x89PNG another one").decode()


def image(data: str = PNG, media_type: str = "image/png", **extra) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    } | extra


def pdf(pages: list[str], **extra) -> dict:
    data = base64.b64encode(make_pdf(pages)).decode()
    source = {"type": "base64", "media_type": "application/pdf", "data": data}
    return {"type": "document", "source": source} | extra


def body(*blocks: dict) -> dict:
    return {
        "model": "m",
        "messages": [{"role": "user", "content": [*blocks, {"type": "text", "text": "q"}]}],
    }


def completion(content: str) -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"message": {"role": "assistant", "content": content}}]}
    )


def vision(description: str, document: bool) -> httpx.Response:
    return completion(json.dumps({"document": document, "description": description}))


def image_url(request: httpx.Request) -> str:
    return json.loads(request.content)["messages"][0]["content"][0]["image_url"]["url"]


def model(request: httpx.Request) -> str:
    return json.loads(request.content)["model"]


class Privatemode:
    """Answers OCR with `ocr(data_url)` and vision with `vision_answer`; counts calls per model."""

    def __init__(self, router: respx.Router) -> None:
        self.calls: dict[str, int] = {OCR_MODEL: 0, VISION_MODEL: 0}
        self.ocr = lambda url: completion("ocr markdown")
        self.vision_answer = vision("a description", document=False)
        self.delay = lambda url: 0.0
        self.in_flight = 0
        self.max_in_flight = 0
        router.post("/v1/chat/completions").mock(side_effect=self._answer)

    async def _answer(self, request: httpx.Request) -> httpx.Response:
        self.calls[model(request)] += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay(image_url(request)))
        finally:
            self.in_flight -= 1
        return self.ocr(image_url(request)) if model(request) == OCR_MODEL else self.vision_answer


@pytest.fixture
def privatemode():
    with respx.mock(base_url=PRIVATEMODE, assert_all_called=False) as router:
        yield Privatemode(router)


@pytest.fixture
async def client():
    async with httpx.AsyncClient(base_url=PRIVATEMODE) as c:
        yield c


def converter(client, concurrency: int = 4, cache_entries: int = 16) -> MediaConverter:
    return MediaConverter(
        client,
        ocr_model=OCR_MODEL,
        vision_model=VISION_MODEL,
        concurrency=concurrency,
        cache_entries=cache_entries,
        render_timeout_s=10,
    )


def first_block(out: dict) -> dict:
    return out["messages"][0]["content"][0]


async def test_a_body_without_binary_blocks_is_returned_unchanged(privatemode):
    original = body()
    before = copy.deepcopy(original)
    out, converted = await convert_media(original, None)
    assert (out, converted) == (before, 0)
    assert original == before
    assert privatemode.calls == {OCR_MODEL: 0, VISION_MODEL: 0}


@pytest.mark.parametrize(
    "source",
    [{"type": "url", "url": "https://example.test/a.pdf"}, {"type": "file", "file_id": "file_1"}],
    ids=["url", "file"],
)
@pytest.mark.parametrize("enabled", [True, False])
async def test_url_and_file_sources_are_refused(privatemode, client, source, enabled):
    block = {"type": "document", "source": source}
    with pytest.raises(BadRequest, match="URL or file id"):
        await convert_media(body(block), converter(client) if enabled else None)
    assert privatemode.calls == {OCR_MODEL: 0, VISION_MODEL: 0}


async def test_a_base64_block_without_conversion_names_the_setting(privatemode):
    with pytest.raises(BadRequest, match="PII_PROXY_MEDIA_ENABLED"):
        await convert_media(body(image()), None)


@pytest.mark.parametrize(
    "block,named",
    [
        (image(media_type="image/bmp"), "image/bmp"),
        (pdf(["x"]) | {"source": pdf(["x"])["source"] | {"media_type": "text/html"}}, "text/html"),
        (
            {"type": "container_upload", "source": {"type": "base64", "data": PNG}},
            "container_upload",
        ),
    ],
    ids=["image media type", "document media type", "block type"],
)
async def test_unsupported_blocks_are_refused_naming_what(privatemode, client, block, named):
    with pytest.raises(BadRequest, match=named):
        await convert_media(body(block), converter(client))
    assert privatemode.calls == {OCR_MODEL: 0, VISION_MODEL: 0}


async def test_invalid_base64_is_refused(privatemode, client):
    with pytest.raises(BadRequest, match="base64"):
        await convert_media(body(image(data="not base64!")), converter(client))


async def test_an_image_that_is_not_a_document_is_described_only(privatemode, client):
    out, converted = await convert_media(body(image()), converter(client))
    assert converted == 1
    assert first_block(out) == {
        "type": "text",
        "text": "[Image, converted to text by pii-proxy]\n\na description",
    }
    assert privatemode.calls == {OCR_MODEL: 0, VISION_MODEL: 1}
    assert out["messages"][0]["content"][1] == {"type": "text", "text": "q"}


async def test_a_document_image_is_described_and_read(privatemode, client):
    privatemode.vision_answer = vision("an invoice", document=True)
    privatemode.ocr = lambda url: (
        completion("| Item | Qty |") if url.endswith(PNG) else completion("?")
    )
    out, _ = await convert_media(body(image()), converter(client))
    assert first_block(out)["text"] == (
        "[Image, converted to text by pii-proxy]\n\nan invoice\n\n| Item | Qty |"
    )
    assert privatemode.calls == {OCR_MODEL: 1, VISION_MODEL: 1}


async def page_labels(pages: list[str]) -> dict[str, str]:
    """Each rendered page's data URL mapped to its 1-based page label."""
    rendered = await render_pdf(make_pdf(pages), timeout_s=10)
    return {
        f"data:image/png;base64,{base64.b64encode(png).decode()}": f"md of page {i}"
        for i, png in enumerate(rendered, 1)
    }


async def test_a_pdf_becomes_each_pages_markdown_in_page_order(privatemode, client):
    pages = ["alpha", "bravo", "charlie"]
    labels = await page_labels(pages)
    privatemode.ocr = lambda url: completion(labels[url])
    # Later pages answer first, so arrival order is not page order.
    order = list(labels)
    privatemode.delay = lambda url: 0.03 * (len(order) - order.index(url))
    out, converted = await convert_media(body(pdf(pages, title="Report")), converter(client))
    assert converted == 1
    assert first_block(out)["text"] == (
        '[PDF "Report", 3 pages, converted to text by pii-proxy]'
        "\n\n--- Page 1 ---\n\nmd of page 1"
        "\n\n--- Page 2 ---\n\nmd of page 2"
        "\n\n--- Page 3 ---\n\nmd of page 3"
    )
    assert privatemode.calls == {OCR_MODEL: 3, VISION_MODEL: 0}


async def test_a_pdf_without_title_and_of_one_page(privatemode, client):
    out, _ = await convert_media(body(pdf(["alpha"])), converter(client))
    assert first_block(out)["text"] == (
        "[PDF, 1 page, converted to text by pii-proxy]\n\n--- Page 1 ---\n\nocr markdown"
    )


async def test_a_pdf_that_cannot_be_rendered_is_refused_without_its_content(privatemode, client):
    data = base64.b64encode(b"secret-looking bytes, not a pdf").decode()
    block = {
        "type": "document",
        "source": {"type": "base64", "media_type": "application/pdf", "data": data},
    }
    with pytest.raises(BadRequest, match="PDF could not be rendered") as raised:
        await convert_media(body(block), converter(client))
    assert raised.value.cause == "PDF could not be rendered"
    assert privatemode.calls == {OCR_MODEL: 0, VISION_MODEL: 0}


async def test_cache_control_stays_on_the_replacement(privatemode, client):
    marker = {"type": "ephemeral"}
    out, converted = await convert_media(body(image(cache_control=marker)), converter(client))
    assert converted == 1
    assert first_block(out) == {
        "type": "text",
        "text": "[Image, converted to text by pii-proxy]\n\na description",
        "cache_control": marker,
    }


async def test_the_same_block_converts_once_and_reads_the_same(privatemode, client):
    media = converter(client)
    out, converted = await convert_media(body(image(), image()), media)
    assert converted == 2
    first, second = out["messages"][0]["content"][:2]
    assert first == second
    again, _ = await convert_media(body(image()), media)
    assert first_block(again) == first
    assert privatemode.calls[VISION_MODEL] == 1


async def test_a_failed_conversion_is_not_cached(privatemode, client):
    media = converter(client)
    privatemode.vision_answer = httpx.Response(500)
    with pytest.raises(PrivatemodeUnavailable):
        await convert_media(body(image()), media)
    privatemode.vision_answer = vision("a description", document=False)
    await convert_media(body(image()), media)
    assert privatemode.calls[VISION_MODEL] == 2


async def test_the_least_recently_used_entry_is_evicted_first(privatemode, client):
    media = converter(client, cache_entries=2)
    a, b, c = image(), image(data=OTHER_PNG), image(data=base64.b64encode(b"third").decode())
    for block in (a, b, a, c):
        await convert_media(body(block), media)
    assert privatemode.calls[VISION_MODEL] == 3
    await convert_media(body(a), media)
    assert privatemode.calls[VISION_MODEL] == 3
    await convert_media(body(b), media)
    assert privatemode.calls[VISION_MODEL] == 4


async def test_calls_in_flight_are_bounded(privatemode, client):
    privatemode.delay = lambda url: 0.02
    await convert_media(
        body(pdf(["a", "b", "c", "d", "e"]), image()), converter(client, concurrency=2)
    )
    assert privatemode.max_in_flight == 2


async def test_request_bodies(client):
    privatemode_requests: list[dict] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        privatemode_requests.append(sent)
        if sent["model"] == VISION_MODEL:
            return vision("d", document=True)
        return completion("m")

    with respx.mock(base_url=PRIVATEMODE) as router:
        router.post("/v1/chat/completions").mock(side_effect=record)
        await convert_media(body(image(media_type="image/jpeg")), converter(client))

    data_url = f"data:image/jpeg;base64,{PNG}"
    vision_request, ocr_request = privatemode_requests
    assert ocr_request == {
        "model": OCR_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": "Convert the document to markdown."},
                ],
            }
        ],
        "temperature": 0,
    }
    assert vision_request["model"] == VISION_MODEL
    assert vision_request["temperature"] == 0
    content = vision_request["messages"][0]["content"]
    assert content[0] == {"type": "image_url", "image_url": {"url": data_url}}
    assert "document" in content[1]["text"]
    schema = vision_request["response_format"]["json_schema"]
    assert vision_request["response_format"]["type"] == "json_schema"
    assert schema["strict"] is True
    assert schema["schema"]["required"] == ["document", "description"]


@pytest.mark.parametrize(
    "failure,error",
    [
        (httpx.ReadTimeout("slow"), PrivatemodeUnavailable),
        (httpx.ConnectError("down"), PrivatemodeUnavailable),
        (httpx.Response(500, text="an invoice for Jane"), PrivatemodeUnavailable),
        (completion("Jane Doe, not json"), PrivatemodeInvalid),
        (completion(json.dumps({"document": "yes", "description": "Jane"})), PrivatemodeInvalid),
        (
            httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
            PrivatemodeInvalid,
        ),
        (httpx.Response(200, json={"error": "Jane"}), PrivatemodeInvalid),
        (httpx.Response(200, text="Jane, not an envelope"), PrivatemodeInvalid),
    ],
    ids=[
        "timeout",
        "transport",
        "http 500",
        "not json",
        "wrong schema",
        "null content",
        "no choices",
        "body not json",
    ],
)
async def test_privatemode_failures_reject_without_content(client, failure, error):
    with respx.mock(base_url=PRIVATEMODE) as router:
        router.post("/v1/chat/completions").mock(side_effect=[failure])
        with pytest.raises(error) as raised:
            await convert_media(body(image()), converter(client))
    assert "Jane" not in raised.value.cause
    assert "media conversion" in raised.value.cause


async def test_a_request_with_more_distinct_blocks_than_the_cache_holds(privatemode, client):
    out, converted = await convert_media(
        body(image(), image(data=OTHER_PNG)), converter(client, cache_entries=1)
    )
    assert converted == 2
    assert out["messages"][0]["content"][1]["type"] == "text"


async def test_overlapping_requests_share_one_conversion(privatemode, client):
    privatemode.delay = lambda url: 0.05
    media = converter(client)
    (first, _), (second, _) = await asyncio.gather(
        convert_media(body(image()), media), convert_media(body(image()), media)
    )
    assert first == second
    assert privatemode.calls == {OCR_MODEL: 0, VISION_MODEL: 1}


@pytest.mark.parametrize(
    "source,named",
    [
        ({"type": "base64", "media_type": "image/png", "data": 1}, "data"),
        ({"type": "base64", "media_type": "image/png"}, "data"),
        ({"type": "base64", "media_type": "image/png", "data": ""}, "data"),
        ({"type": "base64", "media_type": {}, "data": PNG}, "media type"),
        ({"type": "base64", "data": PNG}, "media type"),
    ],
    ids=[
        "data not a string",
        "data missing",
        "data empty",
        "media type not a string",
        "media type missing",
    ],
)
@pytest.mark.parametrize("enabled", [True, False])
async def test_malformed_blocks_are_refused(privatemode, client, source, named, enabled):
    block = {"type": "image", "source": source}
    with pytest.raises(BadRequest, match=named):
        await convert_media(body(block), converter(client) if enabled else None)
    assert privatemode.calls == {OCR_MODEL: 0, VISION_MODEL: 0}


@pytest.mark.parametrize("source_type", ["url", "file", "base64"])
@pytest.mark.parametrize("enabled", [True, False])
async def test_a_binary_source_on_another_block_type_is_refused_naming_it(
    privatemode, client, source_type, enabled
):
    block = {"type": "container_upload", "source": {"type": source_type, "data": PNG}}
    with pytest.raises(BadRequest, match="container_upload"):
        await convert_media(body(block), converter(client) if enabled else None)


async def test_an_unexpected_failure_becomes_a_proxy_error_without_content(
    privatemode, client, monkeypatch, tmp_path
):
    monkeypatch.setattr(media_module, "PDFTOPPM", str(tmp_path / "missing-pdftoppm"))
    with pytest.raises(ProxyError) as raised:
        await convert_media(body(pdf(["Jane"])), converter(client))
    assert raised.value.cause == "media conversion failed (FileNotFoundError)"


async def test_a_pdf_render_that_overruns_is_killed_and_refused(client, monkeypatch, tmp_path):
    slow = tmp_path / "slow-pdftoppm"
    pid_file = tmp_path / "pid"
    slow.write_text(f"#!/bin/sh\necho $$ > {pid_file}\nexec sleep 30\n")
    slow.chmod(0o700)
    monkeypatch.setattr(media_module, "PDFTOPPM", str(slow))
    with pytest.raises(BadRequest, match="PDF could not be rendered"):
        await render_pdf(make_pdf(["a"]), timeout_s=0.3)
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize("pdf_first", [True, False])
async def test_a_privatemode_failure_wins_over_an_unexpected_one(
    privatemode, client, monkeypatch, tmp_path, pdf_first
):
    monkeypatch.setattr(media_module, "PDFTOPPM", str(tmp_path / "missing-pdftoppm"))
    privatemode.vision_answer = httpx.Response(500)
    blocks = [pdf(["Jane"]), image()]
    with pytest.raises(PrivatemodeUnavailable):
        await convert_media(body(*(blocks if pdf_first else blocks[::-1])), converter(client))


async def test_a_request_going_away_does_not_cancel_a_shared_conversion(privatemode, client):
    privatemode.delay = lambda url: 0.1
    media = converter(client)
    first = asyncio.create_task(convert_media(body(image()), media))
    second = asyncio.create_task(convert_media(body(image()), media))
    await asyncio.sleep(0.02)
    first.cancel()
    out, converted = await second
    assert converted == 1
    assert out["messages"][0]["content"][0]["type"] == "text"
    await convert_media(body(image()), media)
    assert privatemode.calls == {OCR_MODEL: 0, VISION_MODEL: 1}
