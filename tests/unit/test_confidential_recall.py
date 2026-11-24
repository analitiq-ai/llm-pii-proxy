"""SC-005 scoring of scripts/e2e/confidential_recall.py. The script itself calls Privatemode; this
tests only how it grades one example."""

import importlib.util

import pytest

from tests.conftest import REPO_ROOT

_spec = importlib.util.spec_from_file_location(
    "confidential_recall", REPO_ROOT / "scripts" / "e2e" / "confidential_recall.py"
)
recall = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(recall)
replaced = recall.replaced

PHRASE = "the only left-handed cardiologist at St. Mary's in Tralee"


@pytest.mark.parametrize(
    "scrubbed",
    [
        "Ask PII_CONTEXT_0001 to review the scan.",
        "Ask the PII_CONTEXT_0001 to review the scan.",
        "Ask the PII_CONTEXT_0001 at PII_CONTEXT_0002 in PII_LOCATION_0001 to review it.",
    ],
    ids=["whole", "leading-article-kept", "pieces"],
)
def test_phrase_counts_as_replaced_when_no_content_word_survives(scrubbed):
    assert replaced(PHRASE, scrubbed)


@pytest.mark.parametrize(
    "scrubbed",
    [
        f"Ask {PHRASE} to review the scan.",
        "Ask the only left-handed cardiologist at PII_CONTEXT_0001 to review the scan.",
        "Ask PII_CONTEXT_0001 in Tralee to review the scan.",
    ],
    ids=["untouched", "role-kept", "place-kept"],
)
def test_phrase_is_not_replaced_while_a_content_word_survives(scrubbed):
    assert not replaced(PHRASE, scrubbed)


def test_word_match_is_whole_word():
    assert replaced("Tralee", "PII_LOCATION_0001 and Traleeside")
