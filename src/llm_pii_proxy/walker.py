"""Which strings of a Messages request are scrubbed (contracts/http-interface.md), and where its
binary blocks are (specs/023-media-to-text/plan.md).

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

type OnLeaf = Callable[[str], str]
type OnBinary = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True, slots=True)
class Leaves:
    texts: list[str]


def collect_leaves(body: dict[str, Any]) -> Leaves:
    texts: list[str] = []

    def record(leaf: str) -> str:
        texts.append(leaf)
        return leaf

    _walk_fields(body, record, _refuse_binary)
    return Leaves(texts)


def apply_leaves(body: dict[str, Any], scrubbed: Sequence[str]) -> dict[str, Any]:
    """A copy of `body` with its leaves, in `collect_leaves` order, replaced by `scrubbed`."""
    expected = len(collect_leaves(body).texts)
    if len(scrubbed) != expected:
        raise ValueError(f"leaf count mismatch: body has {expected}, got {len(scrubbed)}")
    replacements: Iterator[str] = iter(scrubbed)
    return _walk_fields(body, lambda _: next(replacements), _refuse_binary)


def collect_binary_blocks(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Every block holding a binary source, in the order `apply_binary_blocks` replaces them."""
    blocks: list[dict[str, Any]] = []

    def record(block: dict[str, Any]) -> dict[str, Any]:
        blocks.append(block)
        return block

    _walk_fields(body, _keep, record)
    return blocks


def apply_binary_blocks(body: dict[str, Any], replacements: Sequence[Any]) -> dict[str, Any]:
    """A copy of `body` with its binary blocks, in `collect_binary_blocks` order, replaced."""
    expected = len(collect_binary_blocks(body))
    if len(replacements) != expected:
        raise ValueError(f"block count mismatch: body has {expected}, got {len(replacements)}")
    blocks: Iterator[Any] = iter(replacements)
    return _walk_fields(body, _keep, lambda _: next(blocks))


def _keep(leaf: str) -> str:
    return leaf


def _refuse_binary(_: dict[str, Any]) -> Any:
    # Binary blocks are converted to text before the scrub; one still here was never converted.
    raise ValueError("binary block reached the leaf walk")


def _walk_fields(body: dict[str, Any], on_leaf: OnLeaf, on_binary: OnBinary) -> dict[str, Any]:
    out = dict(body)
    # Same field order on every walk, not the body's key order: clients send either.
    for field in SCRUBBED_FIELDS:
        if field in body:
            out[field] = _walk(body[field], on_leaf, on_binary)
    return out


def _walk(node: Any, on_leaf: OnLeaf, on_binary: OnBinary) -> Any:
    if isinstance(node, str):
        return on_leaf(node)
    if isinstance(node, list):
        return [_walk(item, on_leaf, on_binary) for item in node]
    if not isinstance(node, dict):
        return node
    if node.get("type") in _UNTOUCHED_BLOCK_TYPES:
        return node
    source = node.get("source")
    if isinstance(source, dict) and source.get("type") in _BINARY_SOURCE_TYPES:
        return on_binary(node)
    return {
        key: value if key in _UNTOUCHED_KEYS else _walk(value, on_leaf, on_binary)
        for key, value in node.items()
    }
