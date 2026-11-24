import pytest
from hypothesis import given
from hypothesis import strategies as st

from llm_pii_proxy.detection.chunking import chunk


def covered(text, chunks):
    seen = [False] * len(text)
    for c in chunks:
        for i in range(c.start, c.start + len(c.text)):
            seen[i] = True
    return all(seen)


def test_empty_input_gives_no_chunks():
    assert chunk("", 100, overlap=10) == []


def test_small_text_is_one_chunk():
    assert [(c.start, c.text) for c in chunk("a\nb\n", 100, overlap=10)] == [(0, "a\nb\n")]


def test_splits_fall_on_line_boundaries():
    text = "aaaa\nbbbb\ncccc\n"
    out = chunk(text, 10, overlap=2)
    assert [c.text for c in out] == ["aaaa\nbbbb\n", "cccc\n"]
    assert [c.start for c in out] == [0, 10]


def test_long_line_split_hard_with_overlap():
    text = "x" * 25
    out = chunk(text, 10, overlap=3)
    assert [(c.start, len(c.text)) for c in out] == [(0, 10), (7, 10), (14, 10), (21, 4)]


def test_overlap_must_be_smaller_than_chunk():
    with pytest.raises(ValueError):
        chunk("abc", 200, overlap=200)


@given(
    st.text(alphabet=st.sampled_from("ab \n\r,"), max_size=400),
    st.integers(min_value=5, max_value=60),
)
def test_chunks_cover_input_within_limit(text, max_chars):
    out = chunk(text, max_chars, overlap=4)
    for c in out:
        assert 0 < len(c.text) <= max_chars
        assert text[c.start : c.start + len(c.text)] == c.text
    assert covered(text, out)
