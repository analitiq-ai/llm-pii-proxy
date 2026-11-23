import json

import httpx

SESSION = {"x-claude-code-session-id": "sess-1"}


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
