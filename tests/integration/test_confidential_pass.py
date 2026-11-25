"""US2 end to end (T056): Anthropic and Privatemode both mocked with respx."""

import json

import httpx
import pytest
import respx

from llm_pii_proxy.detection.confidential import ConfidentialDetector
from tests.integration.conftest import SESSION, message_response, sent

PRIVATEMODE = "http://privatemode.test"
PHRASE = "the only left-handed cardiologist at St. Mary's in Tralee"
EMAIL = "margaret@brightwater.example"


def findings(*texts: str) -> httpx.Response:
    content = json.dumps({"findings": [{"text": t} for t in texts]})
    return httpx.Response(
        200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
    )


def analysed(route) -> list[list[str]]:
    """Leaves each Privatemode call received, one per line (role values are leaves too)."""
    return [
        json.loads(c.request.content)["messages"][1]["content"].split("\n") for c in route.calls
    ]


@pytest.fixture
def privatemode():
    with respx.mock(base_url=PRIVATEMODE, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def proxy(make_proxy, regex_detector):
    async with httpx.AsyncClient(base_url=PRIVATEMODE) as client:
        yield make_proxy(
            [regex_detector],
            confidential=ConfidentialDetector(client, "gpt-oss-120b", 24_000, 4),
        )


def turn(*texts: str) -> dict:
    roles = ["user", "assistant"]
    messages = [{"role": roles[i % 2], "content": t} for i, t in enumerate(texts)]
    return {"model": "m", "messages": messages}


async def test_phrase_is_replaced_outbound_and_restored_inbound(proxy, upstream, privatemode):
    privatemode.post("/v1/chat/completions").mock(return_value=findings(PHRASE))
    upstream.post("/v1/messages").mock(return_value=message_response("Ask PII_CONTEXT_0001."))
    r = await proxy.post("/v1/messages", json=turn(f"Who is {PHRASE}?"), headers=SESSION)
    assert r.status_code == 200
    assert sent(upstream)["messages"][0]["content"] == "Who is PII_CONTEXT_0001?"
    assert r.json()["content"][0]["text"] == f"Ask {PHRASE}."


async def test_privatemode_sees_placeholders_never_vault_values(proxy, upstream, privatemode):
    route = privatemode.post("/v1/chat/completions").mock(return_value=findings())
    upstream.post("/v1/messages").mock(return_value=message_response("ok"))
    first = f"Mail {EMAIL} about SECRET-ZQ81."
    await proxy.post("/v1/messages", json=turn(first), headers=SESSION)
    # A later turn repeats the vaulted values in new text: still sent only as placeholders (SC-006).
    second = turn(first, "ok", f"Again: {EMAIL}, SECRET-ZQ81.")
    await proxy.post("/v1/messages", json=second, headers=SESSION)
    assert analysed(route) == [
        ["user", "Mail PII_EMAIL_0001 about PII_SECRET_0001."],
        ["assistant", "ok", "Again: PII_EMAIL_0001, PII_SECRET_0001."],
    ]


async def test_second_turn_sends_only_new_leaves(proxy, upstream, privatemode):
    route = privatemode.post("/v1/chat/completions").mock(return_value=findings())
    upstream.post("/v1/messages").mock(return_value=message_response("ok"))
    await proxy.post("/v1/messages", json=turn("first question"), headers=SESSION)
    await proxy.post(
        "/v1/messages", json=turn("first question", "ok", "second question"), headers=SESSION
    )
    assert analysed(route) == [["user", "first question"], ["assistant", "ok", "second question"]]


@pytest.mark.parametrize(
    ("answer", "status"),
    [
        (httpx.ConnectError("refused"), 503),
        (httpx.TimeoutException("timed out"), 503),
        (httpx.Response(500), 503),
        (httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]}), 502),
    ],
    ids=["unreachable", "timeout", "5xx", "invalid"],
)
async def test_failed_pass_blocks_the_request(proxy, upstream, privatemode, answer, status):
    route = privatemode.post("/v1/chat/completions")
    if isinstance(answer, Exception):
        route.mock(side_effect=answer)
    else:
        route.mock(return_value=answer)
    r = await proxy.post("/v1/messages", json=turn(f"Who is {PHRASE}?"), headers=SESSION)
    assert r.status_code == status
    assert r.json()["error"]["type"] == "api_error"
    assert upstream.calls.call_count == 0
    assert "cardiologist" not in r.text


async def test_failed_leaf_is_retried_next_request(proxy, upstream, privatemode):
    route = privatemode.post("/v1/chat/completions")
    route.side_effect = [httpx.Response(500), findings(PHRASE)]
    upstream.post("/v1/messages").mock(return_value=message_response("ok"))
    body = turn(f"Who is {PHRASE}?")
    assert (await proxy.post("/v1/messages", json=body, headers=SESSION)).status_code == 503
    assert (await proxy.post("/v1/messages", json=body, headers=SESSION)).status_code == 200
    assert sent(upstream)["messages"][0]["content"] == "Who is PII_CONTEXT_0001?"
