"""Images and PDFs through the proxy (specs/023-media-to-text/spec.md, US1). Anthropic and
Privatemode are mocked; PDFs are rendered by the real `pdftoppm`."""

import base64
import json
import logging

import httpx
import pytest
import respx

from llm_pii_proxy.detection.confidential import ConfidentialDetector
from llm_pii_proxy.media import MediaConverter
from tests.integration.conftest import SESSION, message_response, sent
from tests.pdf import make_pdf

PRIVATEMODE = "http://privatemode.test"
EMAIL = "jane.doe@acme.example"
PNG = base64.b64encode(b"\x89PNG an invoice").decode()
PATHS = ["/v1/messages", "/v1/messages/count_tokens"]


def image(**extra) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": PNG},
    } | extra


def pdf(pages: list[str]) -> dict:
    data = base64.b64encode(make_pdf(pages)).decode()
    return {
        "type": "document",
        "source": {"type": "base64", "media_type": "application/pdf", "data": data},
    }


def request(*content: dict) -> dict:
    return {
        "model": "m",
        "messages": [
            {"role": "user", "content": [*content, {"type": "text", "text": "Summarise."}]}
        ],
    }


def completion(content: str) -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
    )


def answer_by_model(request_: httpx.Request) -> httpx.Response:
    if json.loads(request_.content)["model"] == "glm-5.3-flash":
        return completion(json.dumps({"document": True, "description": f"An invoice for {EMAIL}"}))
    return completion(f"Bill to: {EMAIL}")


@pytest.fixture
def privatemode():
    with respx.mock(base_url=PRIVATEMODE, assert_all_called=False) as router:
        yield router.post("/v1/chat/completions").mock(side_effect=answer_by_model)


@pytest.fixture
async def media_client():
    async with httpx.AsyncClient(base_url=PRIVATEMODE) as client:
        yield client


def converter(client: httpx.AsyncClient, render_timeout_s: float = 10) -> MediaConverter:
    return MediaConverter(
        client,
        ocr_model="deepseek-ocr-2",
        vision_model="glm-5.3-flash",
        concurrency=4,
        cache_entries=8,
        render_timeout_s=render_timeout_s,
    )


@pytest.fixture
def proxy(make_proxy, regex_detector, media_client):
    return make_proxy([regex_detector], media=converter(media_client))


@pytest.fixture
def upstream_ok(upstream):
    upstream.post("/v1/messages").mock(return_value=message_response("ok"))
    upstream.post("/v1/messages/count_tokens").mock(
        return_value=httpx.Response(200, json={"input_tokens": 7})
    )
    return upstream


def no_binary_and_no_email(body: dict) -> None:
    raw = json.dumps(body)
    assert '"base64"' not in raw and PNG not in raw
    assert EMAIL not in raw


@pytest.mark.parametrize("path", PATHS)
async def test_a_pdf_is_sent_as_scrubbed_page_text_in_its_place(
    proxy, upstream_ok, privatemode, path
):
    r = await proxy.post(path, json=request(pdf(["one", "two"])), headers=SESSION)
    assert r.status_code == 200
    body = sent(upstream_ok)
    no_binary_and_no_email(body)
    first, question = body["messages"][0]["content"]
    assert first["type"] == "text"
    assert first["text"].startswith("[PDF, 2 pages, converted to text by pii-proxy]")
    assert first["text"].count("Bill to: PII_EMAIL_0001") == 2
    assert question == {"type": "text", "text": "Summarise."}
    assert r.headers["x-pii-proxy-scrubbed"].endswith(",media_converted=1")


@pytest.mark.parametrize("path", PATHS)
async def test_a_document_image_is_described_and_read(proxy, upstream_ok, privatemode, path):
    r = await proxy.post(path, json=request(image()), headers=SESSION)
    assert r.status_code == 200
    first = sent(upstream_ok)["messages"][0]["content"][0]
    assert first == {
        "type": "text",
        "text": "[Image, converted to text by pii-proxy]\n\n"
        "An invoice for PII_EMAIL_0001\n\nBill to: PII_EMAIL_0001",
    }
    assert privatemode.call_count == 2


async def test_an_image_that_is_not_a_document_is_described_only(proxy, upstream_ok, privatemode):
    privatemode.side_effect = [completion(json.dumps({"document": False, "description": "A cat."}))]
    r = await proxy.post("/v1/messages", json=request(image()), headers=SESSION)
    assert r.status_code == 200
    assert sent(upstream_ok)["messages"][0]["content"][0]["text"] == (
        "[Image, converted to text by pii-proxy]\n\nA cat."
    )


async def test_images_in_tool_results_and_content_documents_are_converted(
    proxy, upstream_ok, privatemode
):
    tool_result = {"type": "tool_result", "tool_use_id": "toolu_1", "content": [image()]}
    content_doc = {"type": "document", "source": {"type": "content", "content": [image()]}}
    r = await proxy.post("/v1/messages", json=request(tool_result, content_doc), headers=SESSION)
    assert r.status_code == 200
    body = sent(upstream_ok)
    no_binary_and_no_email(body)
    content = body["messages"][0]["content"]
    assert content[0]["content"][0]["type"] == "text"
    assert content[1]["source"]["content"][0]["type"] == "text"
    assert r.headers["x-pii-proxy-scrubbed"].endswith(",media_converted=2")


async def test_a_repeated_image_converts_once_and_is_sent_identically(
    proxy, upstream_ok, privatemode
):
    for _ in range(2):
        r = await proxy.post("/v1/messages", json=request(image()), headers=SESSION)
        assert r.status_code == 200
    assert sent(upstream_ok, 0)["messages"] == sent(upstream_ok, 1)["messages"]
    assert privatemode.call_count == 2  # one vision and one OCR call, for the first request only


async def test_cache_control_stays_on_the_replacement(proxy, upstream_ok, privatemode):
    marker = {"type": "ephemeral"}
    r = await proxy.post("/v1/messages", json=request(image(cache_control=marker)), headers=SESSION)
    assert r.status_code == 200
    assert sent(upstream_ok)["messages"][0]["content"][0]["cache_control"] == marker


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize(
    "block,status,cause",
    [
        (
            {"type": "image", "source": {"type": "url", "url": "https://x.test/a.png"}},
            400,
            "URL or file id",
        ),
        (
            {"type": "document", "source": {"type": "file", "file_id": "file_1"}},
            400,
            "URL or file id",
        ),
        (image() | {"source": image()["source"] | {"media_type": "image/bmp"}}, 400, "image/bmp"),
        (
            {
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": PNG},
            },
            400,
            "PDF could not be rendered",
        ),
    ],
    ids=["url", "file", "media type", "corrupt pdf"],
)
async def test_unconvertible_blocks_are_refused_and_nothing_forwarded(
    proxy, upstream_ok, privatemode, path, block, status, cause
):
    r = await proxy.post(path, json=request(block), headers=SESSION)
    assert r.status_code == status
    assert cause in r.json()["error"]["message"]
    assert not upstream_ok.calls


@pytest.mark.parametrize("path", PATHS)
async def test_a_pdf_render_that_overruns_is_retryable_and_nothing_forwarded(
    make_proxy, regex_detector, media_client, upstream_ok, privatemode, path
):
    proxy = make_proxy([regex_detector], media=converter(media_client, render_timeout_s=0))
    r = await proxy.post(path, json=request(pdf(["one"])), headers=SESSION)
    assert r.status_code == 503
    assert r.headers["retry-after"] == "5"
    assert "PDF rendering timed out" in r.json()["error"]["message"]
    assert not upstream_ok.calls
    assert not privatemode.calls


@pytest.mark.parametrize("path", PATHS)
async def test_privatemode_down_fails_the_request_and_nothing_is_forwarded(
    proxy, upstream_ok, privatemode, path
):
    privatemode.side_effect = httpx.ConnectError("down")
    r = await proxy.post(path, json=request(image()), headers=SESSION)
    assert r.status_code == 503
    assert not upstream_ok.calls


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize(
    "block",
    [image(), image() | {"source": image()["source"] | {"media_type": "image/bmp"}}],
    ids=["png", "unsupported media type"],
)
async def test_without_conversion_a_base64_block_is_refused_naming_the_setting(
    make_proxy, regex_detector, upstream_ok, path, block
):
    proxy = make_proxy([regex_detector])
    r = await proxy.post(path, json=request(block), headers=SESSION)
    assert r.status_code == 400
    assert "PII_PROXY_MEDIA_ENABLED" in r.json()["error"]["message"]
    assert not upstream_ok.calls


async def test_converted_text_reaches_the_confidential_pass(
    make_proxy, regex_detector, media_client, upstream_ok
):
    phrase = "the only left-handed cardiologist in Tralee"
    confidential_seen: list[str] = []

    def answer(request_: httpx.Request) -> httpx.Response:
        sent_ = json.loads(request_.content)
        if sent_["model"] == "glm-5.3-flash":
            return completion(
                json.dumps({"document": False, "description": f"A photo of {phrase}"})
            )
        confidential_seen.append(sent_["messages"][1]["content"])
        return completion(json.dumps({"findings": [{"text": phrase}]}))

    proxy = make_proxy(
        [regex_detector],
        confidential=ConfidentialDetector(media_client, "gpt-oss-120b", 24_000, 4),
        media=converter(media_client),
    )
    with respx.mock(base_url=PRIVATEMODE) as router:
        router.post("/v1/chat/completions").mock(side_effect=answer)
        r = await proxy.post("/v1/messages", json=request(image()), headers=SESSION)
    assert r.status_code == 200
    assert any(phrase in text for text in confidential_seen)
    first = sent(upstream_ok)["messages"][0]["content"][0]["text"]
    assert phrase not in first
    assert "A photo of PII_CONTEXT_0001" in first


async def test_nothing_logged_holds_converted_content(proxy, upstream_ok, privatemode, caplog):
    caplog.set_level(logging.DEBUG)
    r = await proxy.post("/v1/messages", json=request(image(), pdf(["one"])), headers=SESSION)
    assert r.status_code == 200
    assert EMAIL not in caplog.text and "An invoice" not in caplog.text
