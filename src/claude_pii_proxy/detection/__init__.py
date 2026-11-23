"""Detector interface and the span rules all detector output goes through (data-model.md)."""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from claude_pii_proxy.vault import Category


class Stage(StrEnum):
    LOCAL = "local"
    CONFIDENTIAL = "confidential"


@dataclass(frozen=True, slots=True)
class DetectedSpan:
    start: int
    end: int
    category: Category
    stage: Stage

    def __post_init__(self) -> None:
        if not 0 <= self.start < self.end:
            raise ValueError(f"invalid span offsets {self.start}:{self.end}")


class Detector(Protocol):
    async def detect(self, texts: Sequence[str]) -> list[list[DetectedSpan]]:
        """One span list per input text, offsets into that text."""
        ...


# A span that swallows a CSV comma or a line break would remove structure the model needs.
_EDGE_DELIMITERS = frozenset(",;|\"'\t")
_LINE = re.compile(r"[^\r\n]+")


def _is_edge(ch: str) -> bool:
    return ch.isspace() or ch in _EDGE_DELIMITERS


def trim_span(text: str, span: DetectedSpan) -> list[DetectedSpan]:
    if span.end > len(text):
        raise ValueError(f"span end {span.end} past text length {len(text)}")
    out = []
    for line in _LINE.finditer(text, span.start, span.end):
        start, end = line.start(), line.end()
        while start < end and _is_edge(text[start]):
            start += 1
        while end > start and _is_edge(text[end - 1]):
            end -= 1
        if start < end:
            out.append(DetectedSpan(start, end, span.category, span.stage))
    return out


def resolve_overlaps(spans: Iterable[DetectedSpan]) -> list[DetectedSpan]:
    """Longest span wins, then the earliest start. Returns non-overlapping spans sorted by start."""
    kept: list[DetectedSpan] = []
    for candidate in sorted(spans, key=lambda s: (s.start - s.end, s.start)):
        if all(candidate.end <= k.start or candidate.start >= k.end for k in kept):
            kept.append(candidate)
    return sorted(kept, key=lambda s: s.start)
