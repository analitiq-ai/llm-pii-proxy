"""Builds one message from Anthropic stream events, in the shape of a non-streamed response.

Where a stream did not end cleanly the shape differs: a tool block cut off keeps its unparsed input
as a string, and an `error` event is kept under `error`.
"""

import json
from typing import Any

from httpx_sse import ServerSentEvent

_DELTA_FIELDS = {
    "text_delta": "text",
    "thinking_delta": "thinking",
    "signature_delta": "signature",
}


class MessageAssembler:
    def __init__(self) -> None:
        self._message: dict[str, Any] = {}
        self._partial_json: dict[int, str] = {}
        self._error: Any = None
        self._stopped = False

    @property
    def complete(self) -> bool:
        return self._stopped and self._error is None

    def feed(self, event: ServerSentEvent) -> None:
        name = event.event
        if name == "error":
            self._error = json.loads(event.data)
        elif name == "message_start":
            self._message = json.loads(event.data)["message"]
        elif name == "content_block_start":
            data = json.loads(event.data)
            self._message["content"].append(data["content_block"])
        elif name == "content_block_delta":
            self._apply_delta(json.loads(event.data))
        elif name == "content_block_stop":
            self._stop_block(json.loads(event.data)["index"])
        elif name == "message_delta":
            data = json.loads(event.data)
            self._message.update(data["delta"])
            self._message.setdefault("usage", {}).update(data.get("usage", {}))
        elif name == "message_stop":
            self._stopped = True

    def message(self) -> dict[str, Any]:
        message = dict(self._message)
        if self._partial_json:
            content = list(message["content"])
            for index, partial in self._partial_json.items():
                content[index] = content[index] | {"input": partial}
            message["content"] = content
        if self._error is not None:
            message["error"] = self._error
        return message

    def _apply_delta(self, data: dict[str, Any]) -> None:
        index, delta = data["index"], data["delta"]
        kind = delta["type"]
        if kind == "input_json_delta":
            self._partial_json[index] = self._partial_json.get(index, "") + delta["partial_json"]
        elif kind == "citations_delta":
            block = self._message["content"][index]
            block["citations"] = [*(block.get("citations") or []), delta["citation"]]
        elif kind in _DELTA_FIELDS:
            field = _DELTA_FIELDS[kind]
            block = self._message["content"][index]
            block[field] = block.get(field, "") + delta[field]

    def _stop_block(self, index: int) -> None:
        if index not in self._partial_json:
            return
        partial = self._partial_json.pop(index)
        try:
            parsed = json.loads(partial) if partial else {}
        except json.JSONDecodeError:
            parsed = partial
        self._message["content"][index]["input"] = parsed
