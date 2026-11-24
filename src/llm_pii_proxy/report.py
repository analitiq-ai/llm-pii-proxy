"""Scrub report (FR-017): counts only, never values or tokens."""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field

from llm_pii_proxy.detection import Stage
from llm_pii_proxy.vault import Category


def _render(by_category: Mapping[Category, int]) -> str:
    return ",".join(f"{c}={n}" for c, n in sorted(by_category.items()) if n)


@dataclass
class ScrubReport:
    """One request: placeholders applied to the leaves it scrubbed, by category and stage."""

    counts: Counter[tuple[Category, Stage]] = field(default_factory=Counter)
    skipped_binary: int = 0
    unissued: int = 0
    confidential_unlocated: int = 0

    def add(self, category: Category, stage: Stage, n: int = 1) -> None:
        self.counts[(category, stage)] += n

    def by_category(self) -> Counter[Category]:
        out: Counter[Category] = Counter()
        for (category, _), n in self.counts.items():
            out[category] += n
        return out

    def header_value(self) -> str:
        parts = [_render(self.by_category())]
        if self.skipped_binary:
            parts.append(f"skipped_binary={self.skipped_binary}")
        return ",".join(p for p in parts if p) or "none"

    def log_line(self, session_id: str, totals: "SessionTotals") -> str:
        return (
            f"session={session_id} scrubbed={self.header_value()} unissued={self.unissued} "
            f"confidential_unlocated={self.confidential_unlocated} totals={totals.render()}"
        )


@dataclass
class SessionTotals:
    by_category: Counter[Category] = field(default_factory=Counter)

    def update(self, report: ScrubReport) -> None:
        self.by_category.update(report.by_category())

    def render(self) -> str:
        return _render(self.by_category) or "none"
