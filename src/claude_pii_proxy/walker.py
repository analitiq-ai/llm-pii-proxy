"""Which strings of a Messages request are scrubbed (contracts/http-interface.md).

A skip-list, not an allow-list: any string under `system` or `messages` is a leaf unless a rule
below excludes it, so fields the API adds later are scrubbed by default.
"""

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

SCRUBBED_FIELDS = ("system", "messages")
_UNTOUCHED_BLOCK_TYPES = frozenset({"thinking", "redacted_thinking"})
_UNTOUCHED_KEYS = frozenset(
    {"type", "id", "tool_use_id", "name", "signature", "media_type", "cache_control"}
)
_BINARY_SOURCE_TYPES = frozenset({"base64", "url", "file"})


@dataclass(frozen=True, slots=True)
class Leaves:
    texts: list[str]
    skipped_binary: int


def collect_leaves(body: dict[str, Any]) -> Leaves:
    texts: list[str] = []

    def record(leaf: str) -> str:
        texts.append(leaf)
        return leaf

    skipped = _Counter()
    for field in SCRUBBED_FIELDS:
        if field in body:
            _walk(body[field], record, skipped)
    return Leaves(texts, skipped.n)


def apply_leaves(body: dict[str, Any], scrubbed: Sequence[str]) -> dict[str, Any]:
    """A copy of `body` with its leaves, in `collect_leaves` order, replaced by `scrubbed`."""
    expected = len(collect_leaves(body).texts)
    if len(scrubbed) != expected:
        raise ValueError(f"leaf count mismatch: body has {expected}, got {len(scrubbed)}")
    replacements: Iterator[str] = iter(scrubbed)
    return {
        key: _walk(value, lambda _: next(replacements), _Counter())
        if key in SCRUBBED_FIELDS
        else value
        for key, value in body.items()
    }


class _Counter:
    def __init__(self) -> None:
        self.n = 0


def _walk(node: Any, on_leaf: Callable[[str], str], skipped: _Counter) -> Any:
    if isinstance(node, str):
        return on_leaf(node)
    if isinstance(node, list):
        return [_walk(item, on_leaf, skipped) for item in node]
    if not isinstance(node, dict):
        return node
    if node.get("type") in _UNTOUCHED_BLOCK_TYPES:
        return node
    out = {}
    for key, value in node.items():
        if key in _UNTOUCHED_KEYS:
            out[key] = value
        elif (
            key == "source"
            and isinstance(value, dict)
            and value.get("type") in _BINARY_SOURCE_TYPES
        ):
            skipped.n += 1
            out[key] = value
        else:
            out[key] = _walk(value, on_leaf, skipped)
    return out
