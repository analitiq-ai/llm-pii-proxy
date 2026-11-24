import pytest

from claude_pii_proxy.operator_lists import (
    BUILT_IN_NEVER_SCRUB,
    OperatorLists,
    OperatorListsError,
    load,
)
from claude_pii_proxy.vault import Category


def write(tmp_path, body):
    p = tmp_path / "lists.toml"
    p.write_text(body)
    return p


def test_absent_file_means_empty_lists(tmp_path):
    lists = load(tmp_path / "missing.toml")
    assert lists == OperatorLists(always_scrub={}, never_scrub=BUILT_IN_NEVER_SCRUB)


def test_loads_both_lists_in_file_order(tmp_path):
    lists = load(
        write(
            tmp_path,
            'never_scrub = ["Django", "Paris"]\n'
            "[always_scrub]\n"
            'PERSON = ["jdoe", "Jane Roe"]\n'
            'EMAIL = ["me@corp.example"]\n',
        )
    )
    assert lists.always_scrub == {
        Category.PERSON: ("jdoe", "Jane Roe"),
        Category.EMAIL: ("me@corp.example",),
    }
    assert list(lists.always_scrub) == [Category.PERSON, Category.EMAIL]
    assert lists.never_scrub == {"Django", "Paris"} | BUILT_IN_NEVER_SCRUB


@pytest.mark.parametrize(
    "body",
    [
        "not = [toml",
        '[always_scrub]\nLITERAL = ["x"]\n',
        '[always_scrub]\nCONTEXT = ["x"]\n',
        '[always_scrub]\nNOSUCH = ["x"]\n',
        '[always_scrub]\nPERSON = [""]\n',
        'never_scrub = [""]\n',
        'never_scrub = ["Ann"]\n[always_scrub]\nPERSON = ["Ann"]\n',
        'never_scrub = "Ann"\n',
        "[always_scrub]\nPERSON = [1]\n",
        'unknown_key = ["x"]\n',
    ],
)
def test_invalid_file_fails(tmp_path, body):
    with pytest.raises(OperatorListsError):
        load(write(tmp_path, body))


def test_error_message_never_contains_a_value(tmp_path):
    with pytest.raises(OperatorListsError) as exc:
        body = 'never_scrub = ["SecretName"]\n[always_scrub]\nPERSON = ["SecretName"]\n'
        load(write(tmp_path, body))
    assert "SecretName" not in str(exc.value)


def test_built_in_list_is_claude_codes_vocabulary():
    assert {
        "Claude",
        "claude",
        "Claude Code",
        "Claude Agent SDK",
        "Anthropic",
    } <= BUILT_IN_NEVER_SCRUB
    assert {"Claude Opus", "Claude Sonnet", "Claude Haiku", "Claude Fable"} <= BUILT_IN_NEVER_SCRUB


def test_operator_may_always_scrub_a_built_in_term(tmp_path):
    path = tmp_path / "lists.toml"
    path.write_text('[always_scrub]\nPERSON = ["Claude"]\n')
    assert load(path).always_scrub == {Category.PERSON: ("Claude",)}
