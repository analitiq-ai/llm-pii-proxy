import json
from collections.abc import Mapping, Sequence

import httpx
import pytest
from claude_pii_proxy.app import create_app

from claude_pii_proxy.detection import Detector
from claude_pii_proxy.scrub import Pipeline
from claude_pii_proxy.sessions import SessionStore
from claude_pii_proxy.vault import Category
from tests.conftest import UPSTREAM

SESSION = {"x-claude-code-session-id": "sess-1"}


@pytest.fixture
async def make_proxy(upstream):
    """Builds the proxy over the given detectors; returns a client talking to it in-process."""
    clients: list[httpx.AsyncClient] = []

    def make(
        detectors: Sequence[Detector],
        *,
        always_scrub: Mapping[Category, Sequence[str]] | None = None,
        never_scrub: frozenset[str] = frozenset(),
    ) -> httpx.AsyncClient:
        upstream_client = httpx.AsyncClient(base_url=UPSTREAM)
        app = create_app(
            pipeline=Pipeline(detectors, never_scrub),
            sessions=SessionStore(always_scrub or {}, idle_ttl_s=3600),
            upstream=upstream_client,
        )
        proxy = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy")
        clients.extend([upstream_client, proxy])
        return proxy

    yield make
    for client in clients:
        await client.aclose()


def sent(upstream, i: int = -1) -> dict:
    """The JSON body of the i-th request the proxy forwarded upstream."""
    return json.loads(upstream.calls[i].request.content)


def message_response(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
        },
    )
