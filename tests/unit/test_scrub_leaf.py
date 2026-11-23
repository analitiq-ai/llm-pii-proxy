"""Leaf pipeline (contracts/placeholder-format.md, Outbound)."""

from collections.abc import Sequence

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from claude_pii_proxy.detection import DetectedSpan, Stage
from claude_pii_proxy.errors import ScrubFailed
from claude_pii_proxy.report import ScrubReport
from claude_pii_proxy.restore import restore_text
from claude_pii_proxy.scrub import Pipeline
from claude_pii_proxy.vault import Category, Vault
from tests.conftest import RegexDetector


class FixedDetector:
    """Returns given (substring, category) hits for every occurrence in each text."""

    def __init__(self, hits: Sequence[tuple[str, Category]], stage: Stage = Stage.LOCAL):
        self.hits = hits
        self.stage = stage
        self.calls: list[list[str]] = []

    async def detect(self, texts):
        self.calls.append(list(texts))
        out = []
        for text in texts:
            spans = []
            for value, category in self.hits:
                i = text.find(value)
                while i >= 0:
                    spans.append(DetectedSpan(i, i + len(value), category, self.stage))
                    i = text.find(value, i + 1)
            out.append(spans)
        return out


class FailingDetector:
    async def detect(self, texts):
        raise RuntimeError("detector broke")


async def scrub(pipeline, leaves, vault=None, report=None):
    vault = vault if vault is not None else Vault()
    results = await pipeline.scrub(leaves, vault, report or ScrubReport())
    return results, vault


async def test_stage1_escapes_literal_tokens_case_insensitively():
    results, vault = await scrub(Pipeline([]), ["see PII_EMAIL_0001 and pii_person_0042."])
    assert results == ["see PII_LITERAL_0001 and PII_LITERAL_0002."]
    assert vault.value_for("PII_LITERAL_0002") == "pii_person_0042"


async def test_stage2_replaces_known_values_longest_first():
    vault = Vault()
    vault.assign("Ann", Category.PERSON)
    vault.assign("Ann Lee", Category.PERSON)
    results, _ = await scrub(Pipeline([]), ["Ann Lee met Ann."], vault)
    assert results == ["PII_PERSON_0002 met PII_PERSON_0001."]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Al,", "PII_PERSON_0001,"),
        ("(Al)", "(PII_PERSON_0001)"),
        ("Alice", "Alice"),
        ("Al2O3", "Al2O3"),
        ("x_Al", "x_Al"),
    ],
)
async def test_boundary_rule_on_word_edges(text, expected):
    vault = Vault()
    vault.assign("Al", Category.PERSON)
    results, _ = await scrub(Pipeline([]), [text], vault)
    assert results == [expected]


async def test_boundary_rule_unconstrained_at_punctuation_edges():
    vault = Vault()
    vault.assign("@corp.example", Category.EMAIL)
    results, _ = await scrub(Pipeline([]), ["a@corp.example"], vault)
    assert results == ["aPII_EMAIL_0001"]


async def test_stage2_applies_without_detection():
    # A value detected once is replaced again even where a detector would miss it.
    vault = Vault.seeded({Category.PERSON: ["jdoe"]})
    results, _ = await scrub(Pipeline([]), ["/home/jdoe/src"], vault)
    assert results == ["/home/PII_PERSON_0001/src"]


async def test_stage3_assigns_new_placeholders():
    results, vault = await scrub(Pipeline([RegexDetector()]), ["mail a@b.com, key SECRET-X1"])
    assert results == ["mail PII_EMAIL_0001, key PII_SECRET_0001"]
    assert vault.value_for("PII_SECRET_0001") == "SECRET-X1"


async def test_stage3_never_scrub_drops_exact_span():
    det = FixedDetector([("Django", Category.PERSON), ("Ann Lee", Category.PERSON)])
    results, _ = await scrub(
        Pipeline([det], never_scrub=frozenset({"Django"})), ["Django by Ann Lee"]
    )
    assert results == ["Django by PII_PERSON_0001"]


async def test_detected_span_overlapping_known_value_keeps_only_the_rest():
    vault = Vault()
    vault.assign("Margaret", Category.PERSON)
    det = FixedDetector([("Margaret Ellsworth", Category.PERSON)])
    results, _ = await scrub(Pipeline([det]), ["Dear Margaret Ellsworth,"], vault)
    assert results == ["Dear PII_PERSON_0001 PII_PERSON_0002,"]


async def test_spans_are_trimmed_before_assignment():
    det = FixedDetector([(",Margaret Ellsworth,", Category.PERSON)])
    results, vault = await scrub(Pipeline([det]), ["1,Margaret Ellsworth,555"])
    assert results == ["1,PII_PERSON_0001,555"]
    assert vault.value_for("PII_PERSON_0001") == "Margaret Ellsworth"


async def test_all_detectors_called_once_per_batch_with_all_leaves():
    a, b = FixedDetector([]), FixedDetector([])
    await scrub(Pipeline([a, b]), ["one", "two", "three"])
    assert a.calls == b.calls == [["one", "two", "three"]]


async def test_values_found_in_one_leaf_apply_to_later_leaves():
    det = FixedDetector([("Ann Lee", Category.PERSON)])
    results, _ = await scrub(Pipeline([det]), ["Ann Lee", "Ann Lee"])
    assert results == ["PII_PERSON_0001", "PII_PERSON_0001"]


async def test_report_counts_by_category_and_stage():
    report = ScrubReport()
    vault = Vault()
    vault.assign("Ann", Category.PERSON)
    await scrub(Pipeline([RegexDetector()]), ["Ann a@b.com PII_X_0001"], vault, report)
    assert report.counts == {
        (Category.PERSON, Stage.LOCAL): 1,
        (Category.EMAIL, Stage.LOCAL): 1,
        (Category.LITERAL, Stage.LOCAL): 1,
    }


async def test_detector_failure_fails_every_leaf_in_the_batch():
    results, _ = await scrub(Pipeline([RegexDetector(), FailingDetector()]), ["a", "b"])
    assert all(isinstance(r, RuntimeError) for r in results)


async def test_failure_of_one_leaf_does_not_fail_the_others():
    vault = Vault()
    for i in range(9999):
        vault.assign(f"filler{i}", Category.SECRET)
    det = FixedDetector([("SECRET-1", Category.SECRET)])
    results, _ = await scrub(Pipeline([det]), ["plain", "SECRET-1", "also plain"], vault)
    assert results[0] == "plain"
    assert isinstance(results[1], ScrubFailed)
    assert results[2] == "also plain"


# Property: restoration inverts scrubbing for any text, vault and detections.

VALUES = ["Ann Lee", "Al", "a@b.co", "SECRET-Z9", "O'Neil", "x.y", "PII_EMAIL_0001"]
pieces = st.one_of(
    st.sampled_from(VALUES),
    st.sampled_from(["pii_person_0003", "PII_", "_0001", "PII_EMAIL_1", "Alice", "Al2"]),
    st.text(alphabet="abAL_PI0123 ,.@'\n-", max_size=8),
)


@settings(max_examples=300, deadline=None)
@given(
    st.lists(pieces, max_size=12).map("".join),
    st.lists(st.sampled_from(VALUES[:5]), max_size=3),
)
async def test_restore_inverts_scrub(text, known):
    vault = Vault()
    for v in known:
        vault.assign(v, Category.PERSON)
    det = FixedDetector([("Ann Lee", Category.PERSON), ("x.y", Category.ADDRESS)])
    (scrubbed,), _ = await scrub(Pipeline([RegexDetector(), det]), [text], vault)
    assert isinstance(scrubbed, str)
    assert restore_text(scrubbed, vault)[0] == text
