import pytest
from app.ingestion.chunker import chunk_page, chunk_pages, estimate_tokens
from app.ingestion.loader import Page


def test_small_page_single_chunk_keeps_page_number():
    ch = chunk_page(Page("Hello world.\nSecond line.", page_number=7))
    assert len(ch) == 1 and ch[0].page_number == 7


def test_chunks_never_cross_pages_and_are_indexed_sequentially():
    pages = [Page("alpha " * 50, 1), Page("beta " * 50, 2)]
    ch = chunk_pages(pages)
    assert [c.page_number for c in ch] == [1, 2]
    assert [c.chunk_index for c in ch] == [0, 1]
    assert "beta" not in ch[0].text


def test_long_page_split_with_overlap_and_size_limit():
    lines = [f"Line number {i} with some filler words to take up space in the chunk." for i in range(200)]
    ch = chunk_page(Page("\n".join(lines), 1), target_tokens=200, overlap_tokens=40)
    assert len(ch) > 3
    assert all(estimate_tokens(c.text) <= 200 + 40 for c in ch)
    # overlap: the first line of chunk n+1 appears at the end of chunk n
    first_of_next = ch[1].text.split("\n")[0]
    assert first_of_next in ch[0].text


def test_heading_tracking():
    text = "# Unit 1 Basics\nsome text here\nmore text\nUNIT TWO\nother text"
    ch = chunk_page(Page(text, None), target_tokens=1000)
    assert ch[0].heading == "Unit 1 Basics"


def test_oversized_single_line_is_split():
    ch = chunk_page(Page("word " * 3000, 1), target_tokens=300, overlap_tokens=30)
    assert len(ch) > 1
    assert all(estimate_tokens(c.text) <= 330 for c in ch)


def test_empty_page_no_chunks():
    assert chunk_page(Page("   \n  ", 1)) == []


def test_invalid_overlap():
    with pytest.raises(ValueError):
        chunk_page(Page("x", 1), target_tokens=100, overlap_tokens=100)


def test_no_text_lost():
    words = [f"w{i}" for i in range(2000)]
    ch = chunk_page(Page("\n".join(words), 1), target_tokens=150, overlap_tokens=20)
    joined = "\n".join(c.text for c in ch)
    assert all(w in joined for w in words)
