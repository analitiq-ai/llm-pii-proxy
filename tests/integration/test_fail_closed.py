"""Fail closed (SC-007, contracts/http-interface.md error table): nothing is forwarded."""

import httpx
import pytest

from claude_pii_proxy.errors import SecretDetectorUnavailable
from claude_pii_proxy.vault import Category
from tests.integration.conftest import SESSION

CONTENT = "Margaret Ellsworth, margaret@brightwater.example, SECRET-ZQ81"


class Raises:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def detect(self, texts):
        raise self.error


def body(content=CONTENT):
    return {"model": "m", "messages": [{"role": "user", "content": content}]}


def assert_closed(r, upstream, status, error_type):
    assert r.status_code == status
    assert r.json()["error"]["type"] == error_type
    assert upstream.calls.call_count == 0
    for fragment in ("Margaret", "brightwater", "SECRET-ZQ81", "PII_"):
        assert fragment not in r.text


@pytest.fixture(autouse=True)
def upstream_ok(upstream):
    upstream.route().mock(return_value=httpx.Response(200, json={"content": []}))


async def test_presidio_raises_400(make_proxy, regex_detector, upstream):
    proxy = make_proxy([regex_detector, Raises(RuntimeError(f"spaCy broke on {CONTENT}"))])
    r = await proxy.post("/v1/messages", json=body(), headers=SESSION)
    assert_closed(r, upstream, 400, "invalid_request_error")


async def test_secret_detector_crash_503(make_proxy, regex_detector, upstream):
    proxy = make_proxy(
        [regex_detector, Raises(SecretDetectorUnavailable("secret detector crashed"))]
    )
    r = await proxy.post("/v1/messages", json=body(), headers=SESSION)
    assert_closed(r, upstream, 503, "api_error")


async def test_vault_exhausted_400(make_proxy, regex_detector, upstream):
    fillers = tuple(f"filler{i}" for i in range(9999))
    proxy = make_proxy([regex_detector], always_scrub={Category.EMAIL: fillers})
    r = await proxy.post("/v1/messages", json=body(), headers=SESSION)
    assert_closed(r, upstream, 400, "invalid_request_error")


@pytest.mark.parametrize(
    "bad",
    [
        {"model": "m", "messages": 5},
        {"model": "m", "messages": {"role": "user", "content": CONTENT}},
        {"model": "m", "messages": [], "system": {"text": CONTENT}},
        {"model": "m", "messages": [], "system": 3},
    ],
)
async def test_unexpected_json_types_400(make_proxy, regex_detector, upstream, bad):
    proxy = make_proxy([regex_detector])
    r = await proxy.post("/v1/messages", json=bad, headers=SESSION)
    assert_closed(r, upstream, 400, "invalid_request_error")


async def test_one_failing_leaf_fails_the_whole_request(make_proxy, regex_detector, upstream):
    fillers = tuple(f"filler{i}" for i in range(9999))
    proxy = make_proxy([regex_detector], always_scrub={Category.SECRET: fillers})
    payload = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "harmless"},
            {"role": "user", "content": "key SECRET-ZQ81"},
        ],
    }
    r = await proxy.post("/v1/messages", json=payload, headers=SESSION)
    assert_closed(r, upstream, 400, "invalid_request_error")
