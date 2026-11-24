import asyncio
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


async def post_and_disconnect(
    proxy: httpx.AsyncClient, path: str, body: dict, *, after_chunks: int
) -> None:
    """Posts through the proxy app and disconnects once `after_chunks` body chunks were sent, as
    a client under uvicorn (ASGI spec 2.3) would. httpx's in-process transport cannot: it reads
    the whole response before returning."""
    raw = json.dumps(body).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(k.encode(), v.encode()) for k, v in SESSION.items()]
        + [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())],
        "client": ("127.0.0.1", 1),
        "server": ("127.0.0.1", 8787),
    }
    chunks_sent = 0
    enough_sent = asyncio.Event()
    if after_chunks == 0:
        enough_sent.set()
    pending = [{"type": "http.request", "body": raw, "more_body": False}]

    async def receive():
        if pending:
            return pending.pop()
        await enough_sent.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        nonlocal chunks_sent
        if message["type"] == "http.response.body" and message.get("body"):
            chunks_sent += 1
            if chunks_sent >= after_chunks:
                enough_sent.set()
        await asyncio.sleep(0)  # a socket write yields to the loop, where the disconnect lands

    await proxy._transport.app(scope, receive, send)
