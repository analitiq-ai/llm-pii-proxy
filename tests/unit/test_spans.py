import pytest

from llm_pii_proxy.detection import DetectedSpan, Stage, resolve_overlaps, trim_span
from llm_pii_proxy.vault import Category

P = Category.PERSON


def span(start, end, category=P):
    return DetectedSpan(start, end, category, Stage.LOCAL)


def texts(text, spans):
    return [text[s.start : s.end] for s in spans]


@pytest.mark.parametrize("start,end", [(-1, 2), (3, 3), (4, 2)])
def test_invalid_offsets_rejected(start, end):
    with pytest.raises(ValueError):
        span(start, end)


def test_trim_rejects_span_past_text_end():
    with pytest.raises(ValueError):
        trim_span("abc", span(0, 4))


def test_trim_removes_csv_delimiters():
    text = "1,Margaret Ellsworth,555-0100"
    assert texts(text, trim_span(text, span(1, 21))) == ["Margaret Ellsworth"]


@pytest.mark.parametrize(
    "wrapped", ['"Ann Lee"', "'Ann Lee'", " Ann Lee\t", "|Ann Lee;", ", Ann Lee ,"]
)
def test_trim_removes_whitespace_quotes_and_separators(wrapped):
    text = f"x{wrapped}y"
    assert texts(text, trim_span(text, span(1, 1 + len(wrapped)))) == ["Ann Lee"]


def test_trim_splits_at_line_breaks():
    text = "Ann Lee\r\nBob Ray\nCy"
    assert texts(text, trim_span(text, span(0, len(text)))) == ["Ann Lee", "Bob Ray", "Cy"]


def test_span_empty_after_trimming_is_dropped():
    text = 'a, "\n ;b'
    assert trim_span(text, span(1, 7)) == []


def test_trim_keeps_category_and_stage():
    text = " x "
    (out,) = trim_span(text, DetectedSpan(0, 3, Category.SECRET, Stage.CONFIDENTIAL))
    assert (out.category, out.stage) == (Category.SECRET, Stage.CONFIDENTIAL)


def test_overlap_longest_wins():
    spans = [span(0, 5), span(3, 12), span(10, 14)]
    assert resolve_overlaps(spans) == [span(3, 12)]


def test_overlap_tie_earliest_start_wins():
    assert resolve_overlaps([span(4, 8), span(2, 6)]) == [span(2, 6)]


def test_non_overlapping_kept_sorted_by_start():
    spans = [span(10, 12), span(0, 3), span(3, 5)]
    assert resolve_overlaps(spans) == [span(0, 3), span(3, 5), span(10, 12)]
