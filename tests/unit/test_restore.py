"""Inbound restoration and streaming holdback (contracts/placeholder-format.md)."""

import json

from hypothesis import given, settings
from hypothesis import strategies as st

from llm_pii_proxy.restore import (
    MAX_HOLDBACK,
    HoldbackRestorer,
    json_escape,
    raw,
    restore_message,
    restore_text,
)
from llm_pii_proxy.vault import Category, Vault


def make_vault() -> Vault:
    vault = Vault()
    vault.assign("ann@x.com", Category.EMAIL)  # PII_EMAIL_0001
    vault.assign('say "hi"\\now', Category.PERSON)  # PII_PERSON_0001
    vault.assign("PII_EMAIL_0001", Category.LITERAL)  # PII_LITERAL_0001
    vault.assign("v" * 30, Category.NATIONALID)  # PII_NATIONALID_0001
    return vault


def test_restores_case_variants_and_leaves_others():
    text = "a PII_EMAIL_0001 b pii_email_0001 c Pii_Email_0001 d PII_EMAIL_0007 e PII_EMAIL_1"
    out, unissued = restore_text(text, make_vault())
    assert out == "a ann@x.com b ann@x.com c ann@x.com d PII_EMAIL_0007 e PII_EMAIL_1"
    assert unissued == 1


def test_literal_restores_to_token_text():
    assert restore_text("PII_LITERAL_0001", make_vault()) == ("PII_EMAIL_0001", 0)


def test_fixed_width_token_is_self_delimiting():
    assert restore_text("PII_EMAIL_00012O3", make_vault()) == ("ann@x.com2O3", 0)


def test_json_escape_keeps_partial_json_valid():
    out, _ = restore_text('{"msg": "PII_PERSON_0001"}', make_vault(), json_escape)
    assert json.loads(out) == {"msg": 'say "hi"\\now'}


def test_holdback_waits_for_complete_token():
    r = HoldbackRestorer(make_vault(), raw)
    assert r.feed("hello PII_EM") == "hello "
    assert r.held == "PII_EM"
    assert r.feed("AIL_000") == ""
    assert r.feed("1 done") == "ann@x.com done"
    assert r.flush() == ""


def test_flush_emits_incomplete_remainder_as_is():
    r = HoldbackRestorer(make_vault(), raw)
    assert r.feed("end PII_EMAIL_00") == "end "
    assert r.flush() == "PII_EMAIL_00"


def test_non_prefix_is_not_held():
    r = HoldbackRestorer(make_vault(), raw)
    assert r.feed("PII_ABCDEFGHIJK") == "PII_ABCDEFGHIJK"
    assert r.feed("x P") == "x "


def test_holdback_counts_unissued():
    r = HoldbackRestorer(make_vault(), raw)
    r.feed("PII_EMAIL_00")
    r.feed("09 and PII_X_0001")
    r.flush()
    assert r.unissued == 2


VOCAB = [
    "PII_EMAIL_0001",
    "pii_person_0001",
    "PII_LITERAL_0001",
    "PII_NATIONALID_0001",
    "PII_EMAIL_0007",
    "PII_EMAIL_1",
    "PII_",
    "P",
    "PI",
    "_0",
    "ABCDEFGHIJK",
    '"',
    "\\",
    " ",
    "x",
    "9",
]


@settings(max_examples=500, deadline=None)
@given(
    st.lists(st.sampled_from(VOCAB), max_size=20).map("".join),
    st.lists(st.integers(min_value=0, max_value=200), max_size=10),
    st.sampled_from([raw, json_escape]),
)
def test_stream_equals_whole_restore_at_every_split(text, cuts, escape):
    vault = make_vault()
    points = sorted({min(c, len(text)) for c in cuts} | {0, len(text)})
    r = HoldbackRestorer(vault, escape)
    out = []
    for a, b in zip(points, points[1:], strict=False):
        out.append(r.feed(text[a:b]))
        assert len(r.held) <= MAX_HOLDBACK
    out.append(r.flush())
    expected, unissued = restore_text(text, vault, escape)
    assert "".join(out) == expected
    assert r.unissued == unissued


def test_max_holdback_is_18():
    assert MAX_HOLDBACK == 18
    r = HoldbackRestorer(make_vault(), raw)
    r.feed("PII_NATIONALID_000")
    assert len(r.held) == len("PII_NATIONALID_000")


def test_restore_message_skips_thinking():
    body = {
        "id": "msg_1",
        "content": [
            {"type": "thinking", "thinking": "PII_EMAIL_0001", "signature": "s"},
            {"type": "redacted_thinking", "data": "PII_EMAIL_0001"},
            {"type": "text", "text": "to PII_EMAIL_0001"},
            {"type": "tool_use", "id": "t", "name": "W", "input": {"p": ["PII_PERSON_0001"]}},
        ],
        "stop_reason": "end_turn",
    }
    out, unissued = restore_message(body, make_vault())
    assert out["content"][:2] == body["content"][:2]
    assert out["content"][2]["text"] == "to ann@x.com"
    assert out["content"][3]["input"] == {"p": ['say "hi"\\now']}
    assert out["id"] == "msg_1" and unissued == 0
    assert body["content"][2]["text"] == "to PII_EMAIL_0001"
