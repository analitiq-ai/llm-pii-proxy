"""Inbound restoration (contracts/placeholder-format.md, Inbound and Streaming holdback)."""

import json
import re
from collections.abc import Callable
from typing import Any

from claude_pii_proxy.vault import TOKEN_PATTERN, Vault

Escape = Callable[[str], str]

# Longest proper prefix of a token: "PII_" + 10 letters + "_" + 3 digits.
MAX_HOLDBACK = 18
_TOKEN_PREFIX = re.compile(r"P(?:I(?:I(?:_(?:[A-Z]{1,10}(?:_[0-9]{0,3})?)?)?)?)?", re.IGNORECASE)
_UNTOUCHED_BLOCK_TYPES = frozenset({"thinking", "redacted_thinking"})


def raw(value: str) -> str:
    return value


def json_escape(value: str) -> str:
    """For values inserted inside a JSON string, as in streamed tool input."""
    return json.dumps(value)[1:-1]


def restore_text(text: str, vault: Vault, escape: Escape = raw) -> tuple[str, int]:
    """Issued tokens (any case) become their escaped values. Also returns the unissued count."""
    unissued = 0

    def replace(m: re.Match[str]) -> str:
        nonlocal unissued
        value = vault.value_for(m.group())
        if value is None:
            unissued += 1
            return m.group()
        return escape(value)

    return TOKEN_PATTERN.sub(replace, text), unissued


class HoldbackRestorer:
    """Restores a stream by chunks, holding back only a suffix that could still become a token."""

    def __init__(self, vault: Vault, escape: Escape) -> None:
        self._vault = vault
        self._escape = escape
        self.held = ""
        self.unissued = 0

    def feed(self, chunk: str) -> str:
        buffer = self.held + chunk
        cut = _held_start(buffer)
        self.held = buffer[cut:]
        return self._restore(buffer[:cut])

    def flush(self) -> str:
        rest, self.held = self.held, ""
        return self._restore(rest)

    def _restore(self, text: str) -> str:
        out, unissued = restore_text(text, self._vault, self._escape)
        self.unissued += unissued
        return out


def _held_start(buffer: str) -> int:
    """Start of the longest suffix that is a proper token prefix, or len(buffer) if none."""
    for start in range(max(0, len(buffer) - MAX_HOLDBACK), len(buffer)):
        if _TOKEN_PREFIX.fullmatch(buffer, start):
            return start
    return len(buffer)


def restore_message(body: dict[str, Any], vault: Vault) -> tuple[dict[str, Any], int]:
    """A non-streamed response with every string in `content` restored, thinking blocks excepted."""
    unissued = 0

    def walk(node: Any) -> Any:
        nonlocal unissued
        if isinstance(node, str):
            text, n = restore_text(node, vault)
            unissued += n
            return text
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, dict):
            if node.get("type") in _UNTOUCHED_BLOCK_TYPES:
                return node
            return {key: walk(value) for key, value in node.items()}
        return node

    out = {**body, "content": walk(body["content"])} if "content" in body else body
    return out, unissued
