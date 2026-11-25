"""One Privatemode chat completion (specs/023-media-to-text/plan.md § Conversion)."""

import httpx
import pytest
import respx

from llm_pii_proxy.errors import PrivatemodeInvalid, PrivatemodeUnavailable
from llm_pii_proxy.privatemode import complete

PRIVATEMODE = "http://privatemode.test"
PURPOSE = "some purpose"
SECRET = "Jane Doe"


def answer(content: object, finish_reason: object = "stop") -> httpx.Response:
    choice = {"message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}
    return httpx.Response(200, json={"choices": [choice]})


async def ask(outcome: httpx.Response | Exception) -> str | Exception:
    async with httpx.AsyncClient(base_url=PRIVATEMODE) as client:
        with respx.mock(base_url=PRIVATEMODE) as router:
            router.post("/v1/chat/completions").mock(side_effect=[outcome])
            return await complete(client, {"messages": [{"content": SECRET}]}, PURPOSE)


async def test_a_finished_answer_is_its_text():
    assert await ask(answer(f"text about {SECRET}")) == f"text about {SECRET}"


@pytest.mark.parametrize(
    "outcome,error,kind",
    [
        (httpx.ReadTimeout(SECRET), PrivatemodeUnavailable, "timed out"),
        (httpx.ConnectError(SECRET), PrivatemodeUnavailable, "unreachable (ConnectError)"),
        (httpx.Response(500, text=SECRET), PrivatemodeUnavailable, "answered HTTP 500"),
        (httpx.Response(200, text=SECRET), PrivatemodeInvalid, "returned an invalid answer"),
        (
            httpx.Response(200, json={"error": SECRET}),
            PrivatemodeInvalid,
            "returned an invalid answer",
        ),
        (answer(None), PrivatemodeInvalid, "returned an invalid answer"),
        (answer(SECRET, finish_reason="length"), PrivatemodeInvalid, "answer was cut off"),
        (answer(SECRET, finish_reason=None), PrivatemodeInvalid, "answer was cut off"),
    ],
    ids=[
        "timeout",
        "transport",
        "non-2xx",
        "body not json",
        "no choices",
        "content not a string",
        "cut off at the token limit",
        "no finish reason",
    ],
)
async def test_each_failure_names_the_purpose_and_its_kind_only(outcome, error, kind):
    result = await ask(outcome)
    assert type(result) is error
    assert result.cause == f"{PURPOSE} {kind}"
