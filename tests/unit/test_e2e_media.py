"""Tests for converted_files, the SC-001 grader in scripts/e2e/media.py."""

import pytest

from llm_pii_proxy.media import IMAGE_HEADER, pdf_header
from scripts.e2e.media import PAGES, converted_files


def request(*texts: str) -> dict:
    content = [{"type": "text", "text": text} for text in texts]
    return {
        "messages": [
            {"role": "user", "content": [{"type": "tool_result", "content": content}]},
        ]
    }


@pytest.mark.parametrize("title", [None, "invoices.pdf"], ids=["untitled", "titled"])
def test_both_files_are_found_whatever_the_pdf_title(title):
    pdf = pdf_header(title, len(PAGES)) + "\n\n--- Page 1 ---\n\nInvoice"
    image = IMAGE_HEADER + "\n\na page"
    assert converted_files([request(pdf), request(image)]) == {"invoices.pdf", "page.png"}


def test_a_file_missing_upstream_is_not_found():
    assert converted_files([request(IMAGE_HEADER + "\n\na page")]) == {"page.png"}


@pytest.mark.parametrize("title", [None, "invoices.pdf"], ids=["untitled", "titled"])
@pytest.mark.parametrize("pages", [1, len(PAGES) - 1, len(PAGES) + 1])
def test_a_pdf_with_another_page_count_is_not_this_pdf(pages, title):
    pdf = pdf_header(title, pages) + "\n\n--- Page 1 ---\n\nInvoice"
    assert converted_files([request(pdf)]) == set()
