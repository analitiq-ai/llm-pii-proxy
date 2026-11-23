"""Re-sent history keeps its bytes (research R3): thinking signatures depend on it."""

import asyncio
import json
import logging

import httpx

from claude_pii_proxy.detection import DetectedSpan, Stage
from claude_pii_proxy.notice import NOTICE
from claude_pii_proxy.vault import Category
from tests.integration.conftest import SESSION, message_response, sent

SYSTEM = [{"type": "text", "text": "You are Claude Code. Owner: ann@corp.example"}]
CLAUDE_MD = "# CLAUDE.md\nMaintainer: Eleanor Vasquez-Hart, eleanor@corp.example"


class FlakyNames:
    """Non-deterministic like NER: finds the name only on its first call."""

    def __init__(self) -> None:
        self.calls = 0

    async def detect(self, texts):
        self.calls += 1
        out = []
        for t in texts:
            i = t.find("Vasquez-Hart")
            hit = i >= 0 and self.calls == 1
            out.append([DetectedSpan(i, i + 12, Category.PERSON, Stage.LOCAL)] if hit else [])
        return out


def turn(history, user_text):
    return {
        "model": "m",
        "system": SYSTEM,
        "messages": [*history, {"role": "user", "content": user_text}],
    }


async def test_growing_conversation_resends_identical_bytes(make_proxy, regex_detector, upstream):
    proxy = make_proxy([regex_detector, FlakyNames()])
    upstream.post("/v1/messages").mock(return_value=message_response("ok"))
    history: list[dict] = []
    user_texts = [
        CLAUDE_MD,
        "now mail bob@corp.example",
        "and Vasquez-Hart again, ann@corp.example",
    ]
    for text in user_texts:
        await proxy.post("/v1/messages", json=turn(history, text), headers=SESSION)
        history += [
            {"role": "user", "content": text},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "hmm", "signature": "sig"},
                    {"type": "text", "text": "ok"},
                ],
            },
        ]
    bodies = [sent(upstream, i) for i in range(3)]
    for earlier, later in zip(bodies, bodies[1:], strict=False):
        assert later["system"] == earlier["system"]
        n = len(earlier["messages"])
        assert json.dumps(later["messages"][:n]) == json.dumps(earlier["messages"])
    assert bodies[0]["system"][-1] == {"type": "text", "text": NOTICE}
    assert "Vasquez-Hart" not in json.dumps(bodies[2])


async def test_concurrent_subagents_send_identical_shared_leaves(
    make_proxy, regex_detector, upstream
):
    names = FlakyNames()
    proxy = make_proxy([regex_detector, names])
    upstream.post("/v1/messages").mock(return_value=message_response("ok"))

    async def subagent(i):
        body = turn([], f"{CLAUDE_MD}") | {
            "messages": [
                {"role": "user", "content": CLAUDE_MD},
                {"role": "user", "content": f"task {i}"},
            ]
        }
        headers = SESSION | {"x-claude-code-agent-id": f"agent-{i}"}
        return await proxy.post("/v1/messages", json=body, headers=headers)

    await asyncio.gather(*(subagent(i) for i in range(4)))
    bodies = [sent(upstream, i) for i in range(4)]
    assert len({json.dumps(b["system"]) for b in bodies}) == 1
    assert len({b["messages"][0]["content"] for b in bodies}) == 1
    assert "Vasquez-Hart" not in bodies[0]["messages"][0]["content"]


async def test_restart_hint_for_session_joined_mid_conversation(
    make_proxy, regex_detector, upstream, caplog
):
    proxy = make_proxy([regex_detector])
    error = {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "messages.1.content.0: thinking blocks cannot be modified",
        },
    }
    upstream.post("/v1/messages").mock(return_value=httpx.Response(400, json=error))
    history = [
        {"role": "user", "content": "hi ann@corp.example"},
        {"role": "assistant", "content": "hello"},
    ]
    caplog.set_level(logging.INFO)
    r = await proxy.post("/v1/messages", json=turn(history, "go"), headers=SESSION)
    assert r.status_code == 400 and r.json() == error
    hints = [
        rec.getMessage()
        for rec in caplog.records
        if "new Claude Code conversation" in rec.getMessage()
    ]
    assert len(hints) == 1 and "sess-1" in hints[0]
    assert "ann@corp.example" not in caplog.text


async def test_no_restart_hint_for_session_started_fresh(
    make_proxy, regex_detector, upstream, caplog
):
    proxy = make_proxy([regex_detector])
    error = {
        "type": "error",
        "error": {"type": "invalid_request_error", "message": "bad signature"},
    }
    upstream.post("/v1/messages").mock(return_value=httpx.Response(400, json=error))
    caplog.set_level(logging.INFO)
    await proxy.post("/v1/messages", json=turn([], "go"), headers=SESSION)
    assert "new Claude Code conversation" not in caplog.text
