from llm_pii_proxy.detection import Stage
from llm_pii_proxy.report import ScrubReport, SessionTotals
from llm_pii_proxy.vault import Category


def test_header_sorted_by_category_and_summed_over_stages():
    r = ScrubReport()
    r.add(Category.PERSON, Stage.LOCAL)
    r.add(Category.EMAIL, Stage.LOCAL)
    r.add(Category.EMAIL, Stage.CONFIDENTIAL)
    assert r.header_value() == "EMAIL=2,PERSON=1"


def test_header_with_skipped_binary():
    r = ScrubReport()
    r.add(Category.SECRET, Stage.LOCAL)
    r.skipped_binary += 2
    assert r.header_value() == "SECRET=1,skipped_binary=2"


def test_empty_header():
    assert ScrubReport().header_value() == "none"


def test_counts_by_category_and_stage():
    r = ScrubReport()
    r.add(Category.CONTEXT, Stage.CONFIDENTIAL)
    r.add(Category.PERSON, Stage.LOCAL)
    r.add(Category.PERSON, Stage.LOCAL)
    assert r.counts == {
        (Category.CONTEXT, Stage.CONFIDENTIAL): 1,
        (Category.PERSON, Stage.LOCAL): 2,
    }


def test_log_line_has_session_id_and_running_totals():
    totals = SessionTotals()
    first, second = ScrubReport(), ScrubReport()
    first.add(Category.EMAIL, Stage.LOCAL)
    second.add(Category.EMAIL, Stage.LOCAL)
    second.add(Category.PERSON, Stage.LOCAL)
    second.unissued = 1
    totals.update(first)
    totals.update(second)
    line = second.log_line("6f1c-uuid", totals)
    assert line == (
        "session=6f1c-uuid scrubbed=EMAIL=1,PERSON=1 unissued=1 confidential_unlocated=0 "
        "totals=EMAIL=2,PERSON=1"
    )


def test_report_has_no_place_for_values():
    # Counts only: the report API takes categories, never strings from the request.
    r = ScrubReport()
    r.add(Category.EMAIL, Stage.LOCAL)
    rendered = r.header_value() + r.log_line("s", SessionTotals())
    assert "@" not in rendered
