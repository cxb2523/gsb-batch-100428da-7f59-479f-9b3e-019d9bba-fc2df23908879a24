import pytest
from intelligence.chunker import chunk_recursive, chunk_by_headings


def test_recursive_short_text():
    chunks = chunk_recursive("Hello world this is short", max_tokens=100)
    assert len(chunks) == 1
    assert chunks[0]["word_count"] == 5


def test_recursive_splits_long_text():
    text = " ".join(f"word{i}" for i in range(1000))
    chunks = chunk_recursive(text, max_tokens=200, overlap=20)
    assert len(chunks) > 1
    # Each chunk should be roughly 200 words
    for c in chunks[:-1]:
        assert c["word_count"] <= 200


def test_recursive_overlap():
    text = " ".join(f"word{i}" for i in range(100))
    chunks = chunk_recursive(text, max_tokens=30, overlap=5)
    # Chunks should overlap
    assert len(chunks) > 1
    assert chunks[0]["end_word"] > chunks[1]["start_word"]


def test_heading_chunking():
    md = """# Title

Intro paragraph.

## Section One

Content for section one.

## Section Two

Content for section two.

### Subsection

More content here.
"""
    chunks = chunk_by_headings(md)
    assert len(chunks) >= 3
    assert chunks[0]["heading"] == "# Title"
    assert "Intro" in chunks[0]["text"]


def test_heading_chunking_no_headings():
    md = "Just some text without any headings."
    chunks = chunk_by_headings(md)
    assert len(chunks) == 1


def test_heading_preserves_content():
    md = """# Main

Some important content here.

## Sub

More details.
"""
    chunks = chunk_by_headings(md)
    all_text = " ".join(c["text"] for c in chunks)
    assert "important content" in all_text
    assert "More details" in all_text
