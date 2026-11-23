"""Which strings are scrubbed (contracts/http-interface.md)."""

import copy
import json

import pytest

from claude_pii_proxy.walker import apply_leaves, collect_leaves


def upper_all(body):
    leaves = collect_leaves(body)
    return apply_leaves(body, [t.upper() for t in leaves.texts]), leaves


def test_system_string_and_messages_at_any_depth():
    body = {
        "system": "sys text",
        "messages": [
            {"role": "user", "content": "plain"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "block"},
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": [{"type": "text", "text": "nested"}],
                    },
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"cmd": "ls x"}}
                ],
            },
        ],
    }
    out, leaves = upper_all(body)
    # "role" isn't on the skip-list, so its value is a leaf like any other string.
    assert leaves.texts == [
        "sys text",
        "user",
        "plain",
        "user",
        "block",
        "nested",
        "assistant",
        "ls x",
    ]
    assert out["system"] == "SYS TEXT"
    assert out["messages"][1]["content"][1]["content"][0]["text"] == "NESTED"
    assert out["messages"][2]["content"][0]["input"] == {"cmd": "LS X"}
    # structural values untouched
    tool_use = out["messages"][2]["content"][0]
    assert (tool_use["type"], tool_use["id"], tool_use["name"]) == ("tool_use", "toolu_1", "Bash")
    assert out["messages"][1]["content"][1]["tool_use_id"] == "toolu_1"


def test_system_block_list():
    body = {"system": [{"type": "text", "text": "a", "cache_control": {"type": "ephemeral"}}]}
    out, leaves = upper_all(body)
    assert leaves.texts == ["a"]
    assert out["system"] == [{"type": "text", "text": "A", "cache_control": {"type": "ephemeral"}}]


def test_thinking_blocks_byte_identical():
    thinking = {"type": "thinking", "thinking": "secret thoughts", "signature": "sig=="}
    redacted = {"type": "redacted_thinking", "data": "opaque"}
    body = {"messages": [{"role": "assistant", "content": [thinking, redacted]}]}
    out, leaves = upper_all(body)
    assert leaves.texts == ["assistant"]
    blocks = out["messages"][0]["content"]
    assert json.dumps(blocks) == json.dumps([thinking, redacted])


def test_skipped_keys_and_cache_control():
    block = {
        "type": "x",
        "id": "i",
        "tool_use_id": "t",
        "name": "n",
        "signature": "s",
        "media_type": "m",
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
        "text": "t",
    }
    out, leaves = upper_all({"messages": [{"content": [block]}]})
    assert leaves.texts == ["t"]
    assert out["messages"][0]["content"][0] == block | {"text": "T"}


def test_binary_sources_untouched_and_counted():
    image = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "iVBO"},
    }
    url_doc = {"type": "document", "source": {"type": "url", "url": "https://x/y.pdf"}}
    file_doc = {"type": "document", "source": {"type": "file", "file_id": "f1"}}
    text_doc = {
        "type": "document",
        "source": {"type": "text", "media_type": "text/plain", "data": "d"},
    }
    body = {"messages": [{"content": [image, url_doc, file_doc, text_doc]}]}
    out, leaves = upper_all(body)
    assert leaves.skipped_binary == 3
    assert leaves.texts == ["d"]
    assert out["messages"][0]["content"][:3] == [image, url_doc, file_doc]
    assert out["messages"][0]["content"][3]["source"]["data"] == "D"


def test_unknown_block_type_is_scrubbed():
    out, leaves = upper_all({"messages": [{"content": [{"type": "future_block", "body": "z"}]}]})
    assert leaves.texts == ["z"]


def test_keys_never_scrubbed():
    out, leaves = upper_all({"messages": [{"content": [{"type": "x", "input": {"key": "v"}}]}]})
    assert out["messages"][0]["content"][0]["input"] == {"key": "V"}


def test_other_top_level_fields_untouched():
    body = {
        "model": "claude-x",
        "tools": [{"name": "Bash", "description": "run commands as jdoe"}],
        "metadata": {"user_id": "u"},
        "max_tokens": 5,
        "stream": True,
        "thinking": {"type": "enabled", "budget_tokens": 10},
        "future_field": "keep me",
        "messages": [],
    }
    out, leaves = upper_all(body)
    assert leaves.texts == []
    assert out == body


def test_key_order_and_non_strings_preserved():
    body = {"z": 1, "messages": [{"b": None, "a": True, "c": 1.5, "d": "s"}], "a": 2}
    out, _ = upper_all(body)
    assert json.dumps(out) == json.dumps(
        {"z": 1, "messages": [{"b": None, "a": True, "c": 1.5, "d": "S"}], "a": 2}
    )


def test_input_not_mutated():
    body = {"system": "s", "messages": [{"content": [{"type": "text", "text": "t"}]}]}
    before = copy.deepcopy(body)
    upper_all(body)
    assert body == before


@pytest.mark.parametrize("scrubbed", [["only one"], ["a", "b", "c"]])
def test_apply_rejects_wrong_leaf_count(scrubbed):
    with pytest.raises(ValueError, match="leaf count"):
        apply_leaves({"system": "s", "messages": [{"content": "c"}]}, scrubbed)
