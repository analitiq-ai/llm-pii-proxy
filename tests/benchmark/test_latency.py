"""SC-004: added time to first streamed byte. Run with `pytest -m benchmark`.

Upstream answers instantly, so the measured time is the proxy's own: parse, scrub, forward, restore.
Each run uses a new session and distinct content, so the leaf cache never helps.
"""

import json
import statistics
import time

import httpx
import pytest

from llm_pii_proxy.detection.gitleaks import SecretDetector
from llm_pii_proxy.detection.presidio import PresidioDetector
from tests.conftest import FIXTURES
from tests.fixtures.filler import stdlib_code
from tests.integration.conftest import SESSION

pytestmark = pytest.mark.benchmark

RUNS = 10
SSE = (
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"text_delta","text":"ok"}}\n\n'
)


CODE_PER_RECORD = (
    1600  # characters of code between corpus records: about a fifth of the text is PII
)


def corpus_records() -> list[str]:
    return [json.loads(line)["text"] for line in (FIXTURES / "corpus" / "local.jsonl").open()]


def contents(size: int, count: int, code_per_record: int) -> list[str]:
    """`count` distinct texts of about `size` characters: corpus records, each after
    `code_per_record` characters of code."""
    records, code = corpus_records(), stdlib_code()
    out, record, offset = [], 0, 0
    for _ in range(count):
        parts: list[str] = []
        while sum(map(len, parts)) < size:
            parts.append(code[offset : offset + code_per_record])
            parts.append(records[record % len(records)])
            offset = (offset + code_per_record) % (len(code) - code_per_record)
            record += 1
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


async def first_byte_times(proxy, texts: list[str]) -> list[float]:
    times = []
    for i, content in enumerate(texts):
        body = {"model": "m", "stream": True, "messages": [{"role": "user", "content": content}]}
        headers = SESSION | {"x-claude-code-session-id": f"latency-{len(content)}-{i}"}
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


async def test_typical_20kb_under_half_a_second(proxy):
    texts = contents(20_000, RUNS, CODE_PER_RECORD)
    assert report("20 KB typical", await first_byte_times(proxy, texts)) < 0.5


async def test_worst_case_and_100kb_recorded(proxy):
    # Every line PII: the phone matcher's 8 regions dominate (research R5). Recorded, not asserted.
    report("20 KB all PII", await first_byte_times(proxy, contents(20_000, RUNS, 0)))
    texts = contents(100_000, RUNS, CODE_PER_RECORD)
    report("100 KB typical (SC-004: about 2.3 s)", await first_byte_times(proxy, texts))
