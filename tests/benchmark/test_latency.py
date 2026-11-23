"""SC-004: added time to first streamed byte. Run with `pytest -m benchmark`.

Upstream answers instantly, so the measured time is the proxy's own: parse, scrub, forward, restore.
Each run sends content the session has not seen, so the leaf cache never helps.
"""

import json
import statistics
import time

import httpx
import pytest

from claude_pii_proxy.detection.gitleaks import SecretDetector
from claude_pii_proxy.detection.presidio import PresidioDetector
from tests.conftest import FIXTURES
from tests.integration.conftest import SESSION

pytestmark = pytest.mark.benchmark

RUNS = 10
SSE = (
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"text_delta","text":"ok"}}\n\n'
)


def fresh_contents(size: int, count: int) -> list[str]:
    """`count` distinct texts of about `size` characters, built from the labelled corpus."""
    records = [json.loads(line)["text"] for line in (FIXTURES / "corpus" / "local.jsonl").open()]
    out, i = [], 0
    for _ in range(count):
        parts: list[str] = []
        while sum(map(len, parts)) < size:
            parts.append(records[i % len(records)])
            i += 1
        out.append("\n".join(parts))
    return out


@pytest.fixture
async def proxy(make_proxy, presidio_engine, secret_detector_binary, upstream):
    secrets = SecretDetector(secret_detector_binary)
    await secrets.start()
    upstream.post("/v1/messages").mock(
        return_value=httpx.Response(200, content=SSE, headers={"content-type": "text/event-stream"})
    )
    yield make_proxy([PresidioDetector(presidio_engine, min_score=0.4), secrets])
    await secrets.close()


async def first_byte_times(proxy, size: int) -> list[float]:
    times = []
    for i, content in enumerate(fresh_contents(size, RUNS)):
        body = {"model": "m", "stream": True, "messages": [{"role": "user", "content": content}]}
        headers = SESSION | {"x-claude-code-session-id": f"latency-{size}-{i}"}
        start = time.perf_counter()
        async with proxy.stream("POST", "/v1/messages", json=body, headers=headers) as r:
            async for _ in r.aiter_bytes():
                times.append(time.perf_counter() - start)
                break
        assert r.status_code == 200
    return times


def report(label: str, times: list[float]) -> float:
    median = statistics.median(times)
    slowest = max(times)
    print(
        f"\n{label}: median {median * 1000:.0f} ms, max {slowest * 1000:.0f} ms, runs {len(times)}"
    )
    return median


async def test_20kb_under_half_a_second(proxy):
    assert report("20 KB", await first_byte_times(proxy, 20_000)) < 0.5


async def test_100kb_recorded(proxy):
    report("100 KB (SC-004 expects about 2.3 s)", await first_byte_times(proxy, 100_000))
