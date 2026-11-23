"""SC-003: local detection recall on the labelled corpus. Run with `pytest -m benchmark`.

A labelled value counts as found when detected spans of any category cover every non-space
character of it, after overlaps are resolved as the scrubber does: that is what keeps it from being
sent. Category-correct recall is printed too.
"""

import json
from collections import Counter

import pytest

from claude_pii_proxy.detection import resolve_overlaps, trim_span
from claude_pii_proxy.detection.gitleaks import SecretDetector
from claude_pii_proxy.detection.presidio import PresidioDetector
from claude_pii_proxy.vault import Category
from tests.conftest import FIXTURES

pytestmark = pytest.mark.benchmark

CORPUS = FIXTURES / "corpus" / "local.jsonl"
TARGETS = {
    Category.EMAIL: 0.95,
    Category.PHONE: 0.95,
    Category.CARD: 0.95,
    Category.IBAN: 0.95,
    Category.IP: 0.95,
    Category.NATIONALID: 0.95,
    Category.PERSON: 0.85,
    Category.ADDRESS: 0.70,
    Category.SECRET: 0.95,
}


async def test_local_recall(presidio_engine, secret_detector_binary):
    records = [json.loads(line) for line in CORPUS.read_text().splitlines()]
    secrets = SecretDetector(secret_detector_binary)
    await secrets.start()
    try:
        texts = [r["text"] for r in records]
        found_by = [
            await PresidioDetector(presidio_engine, min_score=0.4).detect(texts),
            await secrets.detect(texts),
        ]
    finally:
        await secrets.close()

    total, covered, right_category = Counter(), Counter(), Counter()
    for i, record in enumerate(records):
        text = record["text"]
        # As the scrubber resolves them: trimmed, then the longest of overlapping spans wins.
        detected = resolve_overlaps(
            t for spans in found_by for s in spans[i] for t in trim_span(text, s)
        )
        for label in record["spans"]:
            category = Category(label["category"])
            chars = {j for j in range(label["start"], label["end"]) if not text[j].isspace()}
            hits = [s for s in detected if s.start < label["end"] and s.end > label["start"]]
            total[category] += 1
            if chars <= {j for s in hits for j in range(s.start, s.end)}:
                covered[category] += 1
                right_category[category] += all(s.category == category for s in hits)

    print("\ncategory     n   recall  right-category  target")
    failed = []
    for category, target in TARGETS.items():
        recall = covered[category] / total[category]
        print(
            f"{category:<10} {total[category]:>4}  {recall:6.1%}  "
            f"{right_category[category] / total[category]:13.1%}  {target:.0%}"
        )
        if recall < target:
            failed.append(f"{category} {recall:.1%} < {target:.0%}")
    assert not failed, failed
