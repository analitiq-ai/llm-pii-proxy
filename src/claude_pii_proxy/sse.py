"""Streamed response restoration (contracts/http-interface.md Responses, data-model.md).

Events are forwarded one by one, in order, as they arrive: buffering would starve Claude Code's
stream idle timeout. Only text and tool-input deltas change.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

from httpx_sse import ServerSentEvent

from claude_pii_proxy.report import ScrubReport
from claude_pii_proxy.restore import Escape, HoldbackRestorer, json_escape, raw
from claude_pii_proxy.vault import Vault

# delta type -> (field holding the streamed string, how a restored value is inserted)
_RESTORED_DELTAS: dict[str, tuple[str, Escape]] = {
    "text_delta": ("text", raw),
    "input_json_delta": ("partial_json", json_escape),
}


async def restore_stream(
    events: AsyncIterator[ServerSentEvent], vault: Vault, report: ScrubReport
) -> AsyncIterator[bytes]:
    # Per content block index: its restorer and the delta type it streams.
    blocks: dict[int, tuple[HoldbackRestorer, str]] = {}
    try:
        async for event in events:
            if event.event == "content_block_delta":
                yield _restore_delta(event, vault, blocks)
            elif event.event == "content_block_stop":
                index = json.loads(event.data)["index"]
                if index in blocks:
                    restorer, kind = blocks.pop(index)
                    rest = restorer.flush()
                    report.unissued += restorer.unissued
                    if rest:
                        yield _serialize("content_block_delta", _delta_payload(index, kind, rest))
                yield _encode(event)
            else:
                yield _encode(event)
    finally:
        # Blocks of a stream that ended early still report what they counted.
        report.unissued += sum(restorer.unissued for restorer, _ in blocks.values())


def _restore_delta(
    event: ServerSentEvent, vault: Vault, blocks: dict[int, tuple[HoldbackRestorer, str]]
) -> bytes:
    payload = json.loads(event.data)
    delta = payload["delta"]
    kind = delta.get("type")
    if kind not in _RESTORED_DELTAS:
        return _encode(event)
    field, escape = _RESTORED_DELTAS[kind]
    index = payload["index"]
    if index not in blocks:
        blocks[index] = (HoldbackRestorer(vault, escape), kind)
    restorer, _ = blocks[index]
    delta[field] = restorer.feed(delta[field])
    return _serialize(event.event, payload)


def _delta_payload(index: int, kind: str, value: str) -> dict[str, Any]:
    field, _ = _RESTORED_DELTAS[kind]
    return {"type": "content_block_delta", "index": index, "delta": {"type": kind, field: value}}


def _serialize(event_name: str, payload: dict[str, Any]) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return _encode(ServerSentEvent(event=event_name, data=data))


def _encode(event: ServerSentEvent) -> bytes:
    lines = []
    if event.event:
        lines.append(f"event: {event.event}")
    if event.id is not None:
        lines.append(f"id: {event.id}")
    if event.retry is not None:
        lines.append(f"retry: {event.retry}")
    lines.extend(f"data: {line}" for line in event.data.split("\n"))
    return ("\n".join(lines) + "\n\n").encode()
