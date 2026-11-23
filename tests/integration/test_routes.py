"""Routes, forwarding and headers (contracts/http-interface.md)."""

import gzip
import json

import httpx
import pytest

from tests.integration.conftest import SESSION, message_response, sent

BODY = {"model": "m", "max_tokens": 5, "messages": [{"role": "user", "content": "mail a@b.com"}]}


@pytest.fixture
def proxy(make_proxy, regex_detector):
    return make_proxy([regex_detector])


@pytest.mark.parametrize(
    "method,path",
    [("POST", "/v1/complete"), ("GET", "/v1/messages"), ("POST", "/v1/files"), ("GET", "/")],
)
async def test_unknown_routes_404_and_not_forwarded(proxy, upstream, method, path):
    upstream.route().mock(return_value=httpx.Response(200))
    r = await proxy.request(method, path, json=BODY, headers=SESSION)
    assert r.status_code == 404
    assert r.json()["error"]["type"] == "not_found_error"
    assert upstream.calls.call_count == 0


async def test_hello_and_models_forwarded_unchanged(proxy, upstream):
    upstream.head("/api/hello").mock(return_value=httpx.Response(200, headers={"x-a": "1"}))
    upstream.get("/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "a@b.com"}]})
    )
    assert (await proxy.head("/api/hello")).headers["x-a"] == "1"
    r = await proxy.get("/v1/models?limit=5")
    assert r.json() == {"data": [{"id": "a@b.com"}]}
    assert upstream.calls[-1].request.url.params["limit"] == "5"


async def test_query_string_forwarded(proxy, upstream):
    upstream.post("/v1/messages").mock(return_value=message_response("ok"))
    await proxy.post("/v1/messages?beta=true", json=BODY, headers=SESSION)
    assert upstream.calls[0].request.url.params["beta"] == "true"


@pytest.mark.parametrize("path", ["/v1/messages", "/v1/messages/count_tokens"])
async def test_missing_session_header_400(proxy, upstream, path):
    r = await proxy.post(path, json=BODY)
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "invalid_request_error"
    assert upstream.calls.call_count == 0


@pytest.mark.parametrize("content", [b"not json", b"[1, 2]", b'"s"', b"\xff\xfe"])
async def test_non_object_body_400(proxy, upstream, content):
    r = await proxy.post("/v1/messages", content=content, headers=SESSION)
    assert r.status_code == 400
    assert upstream.calls.call_count == 0


async def test_compressed_body_415(proxy, upstream):
    headers = SESSION | {"content-encoding": "gzip"}
    r = await proxy.post(
        "/v1/messages", content=gzip.compress(json.dumps(BODY).encode()), headers=headers
    )
    assert r.status_code == 415
    assert upstream.calls.call_count == 0


async def test_headers_forwarded_except_hop_by_hop(proxy, upstream):
    upstream.post("/v1/messages").mock(return_value=message_response("ok"))
    headers = SESSION | {
        "x-api-key": "sk-test",
        "authorization": "Bearer t",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "b1",
        "x-claude-code-agent-id": "agent-2",
        "accept-encoding": "gzip, br",
        "connection": "keep-alive",
        "te": "trailers",
    }
    await proxy.post("/v1/messages", json=BODY, headers=headers)
    forwarded = upstream.calls[0].request.headers
    for name in (
        "x-api-key",
        "authorization",
        "anthropic-version",
        "anthropic-beta",
        "x-claude-code-agent-id",
        "x-claude-code-session-id",
    ):
        assert forwarded[name] == headers[name]
    assert forwarded["accept-encoding"] == "identity"
    assert forwarded["host"] == "upstream.test"
    assert "te" not in forwarded
    assert int(forwarded["content-length"]) == len(upstream.calls[0].request.content)


@pytest.mark.parametrize("status", [400, 401, 429, 500, 529])
async def test_non_2xx_forwarded_unmodified(proxy, upstream, status):
    raw = b'{"type":"error","error":{"type":"x","message":"PII_EMAIL_0001 stays"}}'
    upstream.post("/v1/messages").mock(
        return_value=httpx.Response(
            status, content=raw, headers={"retry-after": "7", "request-id": "r"}
        )
    )
    r = await proxy.post("/v1/messages", json=BODY, headers=SESSION)
    assert (r.status_code, r.content) == (status, raw)
    assert r.headers["retry-after"] == "7" and r.headers["request-id"] == "r"
    assert "x-pii-proxy-scrubbed" in r.headers


async def test_scrub_header_on_content_routes(proxy, upstream):
    upstream.post("/v1/messages").mock(return_value=message_response("ok"))
    r = await proxy.post("/v1/messages", json=BODY, headers=SESSION)
    assert r.headers["x-pii-proxy-scrubbed"] == "EMAIL=1"
    r = await proxy.post("/v1/messages", json={"messages": []}, headers=SESSION)
    assert r.headers["x-pii-proxy-scrubbed"] == "none"


async def test_count_tokens_scrubbed_and_response_unchanged(proxy, upstream):
    upstream.post("/v1/messages/count_tokens").mock(
        return_value=httpx.Response(200, json={"input_tokens": 12})
    )
    r = await proxy.post("/v1/messages/count_tokens", json=BODY, headers=SESSION)
    assert r.json() == {"input_tokens": 12}
    assert r.headers["x-pii-proxy-scrubbed"] == "EMAIL=1"
    assert sent(upstream)["messages"][0]["content"] == "mail PII_EMAIL_0001"


async def test_messages_scrubbed_with_notice_and_response_restored(proxy, upstream):
    upstream.post("/v1/messages").mock(return_value=message_response("wrote to PII_EMAIL_0001"))
    r = await proxy.post("/v1/messages", json=BODY, headers=SESSION)
    body = sent(upstream)
    assert body["messages"][0]["content"] == "mail PII_EMAIL_0001"
    assert "PII_EMAIL_0001" in body["system"] and "PII_LITERAL" not in body["system"]
    assert body["model"] == "m" and body["max_tokens"] == 5
    assert r.json()["content"][0]["text"] == "wrote to a@b.com"


async def test_upstream_unreachable_502(proxy, upstream):
    upstream.post("/v1/messages").mock(side_effect=httpx.ConnectError("down"))
    r = await proxy.post("/v1/messages", json=BODY, headers=SESSION)
    assert r.status_code == 502
    assert r.json()["error"]["type"] == "api_error"
