"""Placeholder notice (contracts/http-interface.md, FR-019)."""

import copy

import pytest

from llm_pii_proxy.errors import BadRequest
from llm_pii_proxy.notice import NOTICE, with_notice
from llm_pii_proxy.vault import TOKEN_PATTERN


def test_block_list_gets_one_text_block_appended_last():
    first = {"type": "text", "text": "You are Claude Code.", "cache_control": {"type": "ephemeral"}}
    body = {"system": [first, {"type": "text", "text": "more"}], "messages": []}
    out = with_notice(body)
    assert out["system"][:2] == body["system"]
    assert out["system"][2] == {"type": "text", "text": NOTICE}


def test_string_system_gets_notice_appended():
    out = with_notice({"system": "sys"})
    assert out["system"] == "sys\n\n" + NOTICE


def test_absent_system_is_set_to_notice():
    assert with_notice({"messages": []}) == {"messages": [], "system": NOTICE}


def test_input_not_mutated_and_other_fields_kept():
    body = {"model": "m", "system": [{"type": "text", "text": "a"}], "messages": [1]}
    before = copy.deepcopy(body)
    out = with_notice(body)
    assert body == before
    assert out["model"] == "m" and out["messages"] == [1]


def test_notice_is_constant_and_names_the_token_format():
    assert with_notice({"system": "x"}) == with_notice({"system": "x"})
    assert TOKEN_PATTERN.search(NOTICE)
    for phrase in ("verbatim", "restore", "tool"):
        assert phrase in NOTICE.lower()


@pytest.mark.parametrize("system", [{"type": "text"}, 5, True])
def test_system_of_another_type_is_rejected(system):
    with pytest.raises(BadRequest):
        with_notice({"system": system})
