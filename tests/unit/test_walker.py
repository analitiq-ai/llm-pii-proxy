"""Which strings are scrubbed (contracts/http-interface.md)."""

import copy
import json

import pytest

from llm_pii_proxy.walker import (
    apply_binary_blocks,
    apply_leaves,
    collect_binary_blocks,
    collect_leaves,
)


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


IMAGE = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBO"}}
URL_DOC = {"type": "document", "source": {"type": "url", "url": "https://x/y.pdf"}}
FILE_DOC = {"type": "document", "source": {"type": "file", "file_id": "f1"}}


@pytest.mark.parametrize("block", [IMAGE, URL_DOC, FILE_DOC], ids=["base64", "url", "file"])
def test_a_binary_source_reaching_the_leaf_walk_raises(block):
    # Binary blocks are replaced before the scrub; one that is still there was never converted.
    with pytest.raises(ValueError, match="binary"):
        collect_leaves({"messages": [{"content": [{"type": "text", "text": "t"}, block]}]})


def test_a_text_source_document_is_scrubbed():
    text_doc = {
        "type": "document",
        "source": {"type": "text", "media_type": "text/plain", "data": "d"},
    }
    out, leaves = upper_all({"messages": [{"content": [text_doc]}]})
    assert leaves.texts == ["d"]
    assert out["messages"][0]["content"][0]["source"]["data"] == "D"


def _nested_body():
    content_doc = {
        "type": "document",
        "source": {"type": "content", "content": [{"type": "text", "text": "c"}, URL_DOC]},
    }
    return {
        "system": [{"type": "text", "text": "s"}],
        "messages": [
            {"role": "user", "content": [IMAGE, {"type": "text", "text": "t"}]},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "x",
                        "signature": "sig",
                        "source": IMAGE["source"],
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_1", "content": [FILE_DOC]},
                    content_doc,
                ],
            },
        ],
    }


def test_binary_blocks_collected_in_order_at_any_depth_thinking_untouched():
    assert collect_binary_blocks(_nested_body()) == [IMAGE, FILE_DOC, URL_DOC]


def test_binary_blocks_replaced_in_place():
    body = _nested_body()
    before = copy.deepcopy(body)
    out = apply_binary_blocks(body, [{"type": "text", "text": f"r{i}"} for i in range(3)])
    assert body == before
    assert out["messages"][0]["content"][0] == {"type": "text", "text": "r0"}
    assert out["messages"][1] == body["messages"][1]
    assert out["messages"][2]["content"][0]["content"] == [{"type": "text", "text": "r1"}]
    assert out["messages"][2]["content"][1]["source"]["content"][1] == {
        "type": "text",
        "text": "r2",
    }
    assert collect_binary_blocks(out) == []
    assert collect_leaves(out).texts == [
        "s",
        "user",
        "r0",
        "t",
        "assistant",
        "user",
        "r1",
        "c",
        "r2",
    ]


@pytest.mark.parametrize("n", [2, 4])
def test_apply_binary_blocks_rejects_wrong_count(n):
    with pytest.raises(ValueError, match="block count"):
        apply_binary_blocks(_nested_body(), [{"type": "text", "text": "r"}] * n)


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


@pytest.mark.parametrize("order", [("system", "messages"), ("messages", "system")])
def test_each_scrubbed_leaf_returns_to_its_own_place_whatever_the_key_order(order):
    parts = {
        "system": [{"type": "text", "text": "sys"}],
        "messages": [{"role": "user", "content": [{"type": "text", "text": "msg"}]}],
    }
    body = {"model": "m", **{k: parts[k] for k in order}}
    texts = collect_leaves(body).texts
    out = apply_leaves(body, [f"<{t}>" for t in texts])
    assert out["system"] == [{"type": "text", "text": "<sys>"}]
    assert out["messages"] == [{"role": "<user>", "content": [{"type": "text", "text": "<msg>"}]}]
    assert list(out) == list(body)
