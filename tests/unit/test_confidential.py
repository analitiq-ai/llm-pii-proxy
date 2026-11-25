"""Confidential detection pass (contracts/confidential-detection.md). Privatemode is mocked."""

import asyncio
import json

import httpx
import pytest
import respx

from llm_pii_proxy.detection import Stage
from llm_pii_proxy.detection.chunking import chunk
from llm_pii_proxy.detection.confidential import ConfidentialDetector
from llm_pii_proxy.errors import PrivatemodeInvalid, PrivatemodeUnavailable
from llm_pii_proxy.report import ScrubReport
from llm_pii_proxy.scrub import Pipeline
from llm_pii_proxy.vault import Category, Vault
from tests.conftest import RegexDetector

PRIVATEMODE = "http://privatemode.test"
COMPLETIONS = f"{PRIVATEMODE}/v1/chat/completions"
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["findings"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text"],
                "properties": {"text": {"type": "string", "minLength": 1}},
            },
        }
    },
}


def answer(*findings: str, content: str | None = None) -> httpx.Response:
    if content is None:
        content = json.dumps({"findings": [{"text": f} for f in findings]})
    message = {"role": "assistant", "content": content}
    return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "stop"}]})


def user_text(request: httpx.Request) -> str:
    return json.loads(request.content)["messages"][1]["content"]


@pytest.fixture
def privatemode():
    with respx.mock(base_url=PRIVATEMODE, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def client():
    async with httpx.AsyncClient(base_url=PRIVATEMODE) as c:
        yield c


def detector(client, chunk_chars=24_000, concurrency=4):
    return ConfidentialDetector(client, "gpt-oss-120b", chunk_chars, concurrency)


def found(text, spans):
    assert isinstance(spans, list)
    return [(text[s.start : s.end], s.category, s.stage) for s in spans]


async def test_request_body_matches_contract(privatemode, client):
    route = privatemode.post("/v1/chat/completions").mock(return_value=answer())
    await detector(client).detect(["Ask PII_PERSON_0001 about the rota."])
    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "gpt-oss-120b"
    system, user = body["messages"]
    assert system["role"] == "system" and "PII_<CATEGORY>_<NNNN>" in system["content"]
    assert user == {"role": "user", "content": "Ask PII_PERSON_0001 about the rota."}
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "pii_findings", "strict": True, "schema": SCHEMA},
    }
    assert body["temperature"] == 0
    assert body["frequency_penalty"] == 0.1
    assert body["reasoning_effort"] == "low"
    assert body["max_completion_tokens"] == 4096


async def test_leaves_are_packed_into_chunks_from_the_shared_chunker(privatemode, client):
    route = privatemode.post("/v1/chat/completions").mock(return_value=answer())
    leaves = [f"leaf {i}: " + "word " * 40 for i in range(6)]
    await detector(client, chunk_chars=500).detect(leaves)
    sent = sorted(user_text(c.request) for c in route.calls)
    assert sent == sorted(c.text for c in chunk("\n".join(leaves), 500))
    assert len(sent) > 1 and all(len(s) <= 500 for s in sent)


async def test_at_most_concurrency_chunks_in_flight(privatemode, client):
    in_flight = peak = 0

    async def slow(request):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return answer()

    privatemode.post("/v1/chat/completions").mock(side_effect=slow)
    await detector(client, chunk_chars=300, concurrency=2).detect(["x" * 250] * 8)
    assert peak == 2


async def test_every_exact_occurrence_becomes_a_context_span(privatemode, client):
    privatemode.post("/v1/chat/completions").mock(return_value=answer("the mayor's chauffeur"))
    leaves = [
        "Rows of the mayor's chauffeur; again the mayor's chauffeur.",
        "Unrelated text.",
        "Ask the mayor's chauffeur.",
    ]
    results = (await detector(client).detect(leaves)).leaves
    span = ("the mayor's chauffeur", Category.CONTEXT, Stage.CONFIDENTIAL)
    assert found(leaves[0], results[0]) == [span, span]
    assert found(leaves[1], results[1]) == []
    assert found(leaves[2], results[2]) == [span]


async def test_occurrence_across_a_leaf_boundary_is_not_a_span(privatemode, client):
    privatemode.post("/v1/chat/completions").mock(return_value=answer("end\nstart"))
    detection = await detector(client).detect(["the end", "start here"])
    assert detection.leaves == [[], []] and detection.unlocated == 0


async def test_finding_not_in_the_chunk_is_dropped_and_counted(privatemode, client):
    privatemode.post("/v1/chat/completions").mock(
        return_value=answer("the town's only vet", "the mayors chauffeur")
    )
    text = "Call the town's only vet, then the mayor's chauffeur."
    detection = await detector(client).detect([text])
    assert found(text, detection.leaves[0]) == [
        ("the town's only vet", Category.CONTEXT, Stage.CONFIDENTIAL)
    ]
    assert detection.unlocated == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, json={"error": "rate limited"}),
        httpx.Response(402, json={"error": "quota locked"}),
        httpx.Response(500),
        httpx.TimeoutException("timed out"),
        httpx.ConnectError("refused"),
    ],
    ids=["429", "402", "500", "timeout", "connect"],
)
async def test_unreachable_or_non_2xx_is_unavailable(privatemode, client, response):
    route = privatemode.post("/v1/chat/completions")
    route.mock(side_effect=response) if isinstance(response, Exception) else route.mock(
        return_value=response
    )
    (result,) = (await detector(client).detect(["Ask the mayor's chauffeur."])).leaves
    assert isinstance(result, PrivatemodeUnavailable)


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not json",
        '{"findings": [{"text": "x"}',
        "{}",
        '{"findings": "x"}',
        '{"findings": [{"text": ""}]}',
        '{"findings": [{"text": "x", "why": "y"}]}',
        '{"findings": [], "extra": 1}',
    ],
    ids=[
        "empty",
        "prose",
        "truncated",
        "no-findings",
        "wrong-type",
        "empty-text",
        "extra-item-key",
        "extra-key",
    ],
)
async def test_invalid_answer_is_invalid(privatemode, client, content):
    privatemode.post("/v1/chat/completions").mock(return_value=answer(content=content))
    (result,) = (await detector(client).detect(["Ask the mayor's chauffeur."])).leaves
    assert isinstance(result, PrivatemodeInvalid)


async def test_failed_chunk_fails_only_the_leaves_it_covers(privatemode, client):
    def by_chunk(request):
        return httpx.Response(500) if "second" in user_text(request) else answer("first")

    privatemode.post("/v1/chat/completions").mock(side_effect=by_chunk)
    leaves = ["first " + "a" * 240, "second " + "b" * 240]
    first, second = (await detector(client, chunk_chars=260).detect(leaves)).leaves
    assert isinstance(first, list) and isinstance(second, PrivatemodeUnavailable)


async def test_error_causes_never_quote_content(privatemode, client):
    privatemode.post("/v1/chat/completions").mock(
        return_value=answer(content="the mayor's chauffeur is not JSON")
    )
    (result,) = (await detector(client).detect(["Ask the mayor's chauffeur."])).leaves
    assert isinstance(result, PrivatemodeInvalid) and "chauffeur" not in result.cause


# Pipeline: the pass runs on locally scrubbed text; findings go through the vault (FR-012, FR-013).


def pipeline(client, local=None, never_scrub=frozenset()):
    local = local or RegexDetector()
    return Pipeline([local], never_scrub=never_scrub, confidential=detector(client))


async def test_pass_sees_only_locally_scrubbed_text(privatemode, client):
    route = privatemode.post("/v1/chat/completions").mock(return_value=answer())
    vault = Vault()
    await pipeline(client).scrub(
        ["Mail jane@acme.io, the mayor's chauffeur."], vault, ScrubReport()
    )
    assert user_text(route.calls.last.request) == "Mail PII_EMAIL_0001, the mayor's chauffeur."


async def test_findings_become_context_placeholders_and_are_counted(privatemode, client):
    privatemode.post("/v1/chat/completions").mock(return_value=answer("the mayor's chauffeur"))
    vault, report = Vault(), ScrubReport()
    (out,) = await pipeline(client).scrub(
        ["Mail jane@acme.io, the mayor's chauffeur."], vault, report
    )
    assert out == "Mail PII_EMAIL_0001, PII_CONTEXT_0001."
    assert vault.token_for("the mayor's chauffeur") == "PII_CONTEXT_0001"
    assert report.counts[(Category.CONTEXT, Stage.CONFIDENTIAL)] == 1


async def test_placeholder_inside_a_finding_is_cut_out(privatemode, client):
    privatemode.post("/v1/chat/completions").mock(
        return_value=answer("PII_EMAIL_0001 for the mayor's chauffeur")
    )
    vault = Vault()
    (out,) = await pipeline(client).scrub(
        ["Mail jane@acme.io for the mayor's chauffeur."], vault, ScrubReport()
    )
    assert out == "Mail PII_EMAIL_0001 PII_CONTEXT_0001."
    assert vault.token_for("for the mayor's chauffeur") == "PII_CONTEXT_0001"


async def test_never_scrub_applies_to_findings(privatemode, client):
    privatemode.post("/v1/chat/completions").mock(return_value=answer("Dr Who"))
    (out,) = await pipeline(client, never_scrub=frozenset({"Dr Who"})).scrub(
        ["Watch Dr Who tonight."], Vault(), ScrubReport()
    )
    assert out == "Watch Dr Who tonight."


async def test_unlocated_findings_are_reported(privatemode, client):
    privatemode.post("/v1/chat/completions").mock(return_value=answer("paraphrased person"))
    report = ScrubReport()
    await pipeline(client).scrub(["Ask the mayor's chauffeur."], Vault(), report)
    assert report.confidential_unlocated == 1


async def test_value_vaulted_during_the_pass_gets_its_token(privatemode, client):
    # A concurrent request adds a value while this one waits on Privatemode, which misses it here.
    vault = Vault()

    async def concurrent_add(request):
        vault.assign("the mayor's chauffeur", Category.CONTEXT)
        return answer()

    privatemode.post("/v1/chat/completions").mock(side_effect=concurrent_add)
    (out,) = await pipeline(client).scrub(["Ask the mayor's chauffeur."], vault, ScrubReport())
    assert out == "Ask PII_CONTEXT_0001."


async def test_failed_pass_fails_the_leaf(privatemode, client):
    privatemode.post("/v1/chat/completions").mock(return_value=httpx.Response(503))
    (out,) = await pipeline(client).scrub(["Ask the mayor's chauffeur."], Vault(), ScrubReport())
    assert isinstance(out, PrivatemodeUnavailable)


async def test_locally_failed_leaf_is_not_sent(privatemode, client):
    route = privatemode.post("/v1/chat/completions").mock(return_value=answer())

    class Boom:
        async def detect(self, texts):
            raise RuntimeError("detector down")

    (out,) = await pipeline(client, local=Boom()).scrub(["Ask anyone."], Vault(), ScrubReport())
    assert isinstance(out, RuntimeError) and not route.called


async def test_a_cut_off_answer_is_invalid(privatemode, client):
    cut_off = {"role": "assistant", "content": '{"findings": [{"text": "the mayor'}
    privatemode.post("/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": cut_off, "finish_reason": "length"}]}
        )
    )
    (result,) = (await detector(client).detect(["Ask the mayor's chauffeur."])).leaves
    assert isinstance(result, PrivatemodeInvalid)
    assert result.cause.startswith("confidential pass") and "cut off" in result.cause
