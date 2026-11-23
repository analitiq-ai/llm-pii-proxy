"""Outbound scrubbing: the three-stage leaf pipeline (contracts/placeholder-format.md, Outbound)."""

import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache

from claude_pii_proxy.detection import DetectedSpan, Detector, Stage, resolve_overlaps, trim_span
from claude_pii_proxy.report import ScrubReport
from claude_pii_proxy.vault import TOKEN_PATTERN, Category, Vault

# Per-leaf outcome: the scrubbed text, or why the leaf failed (its request then fails closed).
LeafResult = str | Exception


@dataclass(frozen=True)
class Pipeline:
    detectors: Sequence[Detector]
    never_scrub: frozenset[str] = field(default_factory=frozenset)

    async def scrub(
        self, leaves: Sequence[str], vault: Vault, report: ScrubReport
    ) -> list[LeafResult]:
        """Scrub cache-miss leaves. Detection is one batched call per detector, run concurrently."""
        try:
            per_detector = await asyncio.gather(*(d.detect(leaves) for d in self.detectors))
        except Exception as e:  # every leaf of the batch fails closed with this error
            return [e] * len(leaves)
        results: list[LeafResult] = []
        # No await from here on: each leaf sees the vault as it is now, including values that
        # concurrent requests and earlier leaves of this batch added.
        for i, leaf in enumerate(leaves):
            detected = [spans[i] for spans in per_detector]
            try:
                results.append(self._scrub_leaf(leaf, detected, vault, report))
            except Exception as e:  # this leaf fails closed; the others stand
                results.append(e)
        return results

    def _scrub_leaf(
        self,
        text: str,
        detected: Sequence[Sequence[DetectedSpan]],
        vault: Vault,
        report: ScrubReport,
    ) -> str:
        fixed = resolve_overlaps(_literal_spans(text) + _known_spans(text, vault))
        new = [
            piece
            for spans in detected
            for span in spans
            for trimmed in trim_span(text, span)
            if text[trimmed.start : trimmed.end] not in self.never_scrub
            for piece in _subtract(text, trimmed, fixed)
        ]
        replacements = sorted(fixed + resolve_overlaps(new), key=lambda r: r.start)
        # Assign every token before emitting or counting anything: a leaf fails whole or not at all.
        tokens = [vault.assign(text[r.start : r.end], r.category) for r in replacements]
        out, pos = [], 0
        for r, token in zip(replacements, tokens, strict=True):
            out.append(text[pos : r.start])
            out.append(token)
            pos = r.end
            report.add(_category_of(token), r.stage)
        out.append(text[pos:])
        return "".join(out)


def _literal_spans(text: str) -> list[DetectedSpan]:
    return [
        DetectedSpan(m.start(), m.end(), Category.LITERAL, Stage.LOCAL)
        for m in TOKEN_PATTERN.finditer(text)
    ]


def _known_spans(text: str, vault: Vault) -> list[DetectedSpan]:
    present = tuple(sorted({v for v, _ in vault.items() if v in text}, key=lambda v: (-len(v), v)))
    if not present:
        return []
    spans = []
    for m in _known_value_pattern(present).finditer(text):
        token = vault.token_for(m.group())
        assert token is not None
        spans.append(DetectedSpan(m.start(), m.end(), _category_of(token), Stage.LOCAL))
    return spans


@lru_cache(maxsize=256)
def _known_value_pattern(values: tuple[str, ...]) -> re.Pattern[str]:
    """Longest first. A word-character edge of a value may not continue into a word character."""

    def alternative(v: str) -> str:
        before = r"(?<!\w)" if re.match(r"\w", v[0]) else ""
        after = r"(?!\w)" if re.match(r"\w", v[-1]) else ""
        return before + re.escape(v) + after

    return re.compile("|".join(alternative(v) for v in values))


def _subtract(text: str, span: DetectedSpan, fixed: Sequence[DetectedSpan]) -> list[DetectedSpan]:
    """Parts of a detected span not already covered by a literal or known value, re-trimmed."""
    pieces, start = [], span.start
    for f in fixed:
        if f.end <= start or f.start >= span.end:
            continue
        if f.start > start:
            pieces.append(DetectedSpan(start, f.start, span.category, span.stage))
        start = max(start, f.end)
    if start < span.end:
        pieces.append(DetectedSpan(start, span.end, span.category, span.stage))
    return [t for p in pieces for t in trim_span(text, p)]


def _category_of(token: str) -> Category:
    m = TOKEN_PATTERN.fullmatch(token)
    assert m is not None
    return Category(m.group(1).upper())
