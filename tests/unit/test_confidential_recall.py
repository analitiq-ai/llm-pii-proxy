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


def test_alone_by_default():
    assert recall.session_leaves("note", "x" * 100, index=0, context_chars=0) == ["note"]


def test_example_follows_a_context_leaf_of_the_given_size():
    filler = "".join(chr(97 + i % 26) for i in range(1000))
    first = recall.session_leaves("note", filler, index=0, context_chars=300)
    second = recall.session_leaves("note", filler, index=1, context_chars=300)
    assert [len(first[0]), first[1]] == [300, "note"]
    assert first[0] in filler and second[0] in filler and first[0] != second[0]


async def test_unavailable_answer_is_retried_after_a_wait():
    from claude_pii_proxy.errors import ConfidentialUnavailable

    outcomes = [ConfidentialUnavailable("HTTP 429"), ConfidentialUnavailable("HTTP 429"), "ok"]
    waits = []

    async def scrub():
        return outcomes.pop(0)

    async def sleep(s):
        waits.append(s)

    assert await recall.with_retries(scrub, attempts=3, wait_s=20, sleep=sleep) == "ok"
    assert waits == [20, 20]


async def test_gives_up_with_the_last_failure():
    from claude_pii_proxy.errors import ConfidentialUnavailable

    async def scrub():
        return ConfidentialUnavailable("HTTP 429")

    async def sleep(s):
        pass

    result = await recall.with_retries(scrub, attempts=2, wait_s=1, sleep=sleep)
    assert isinstance(result, ConfidentialUnavailable)


async def test_other_failures_are_not_retried():
    calls = []

    async def scrub():
        calls.append(1)
        return RuntimeError("bug")

    async def sleep(s):
        raise AssertionError("must not wait")

    assert isinstance(
        await recall.with_retries(scrub, attempts=3, wait_s=1, sleep=sleep), RuntimeError
    )
    assert len(calls) == 1
