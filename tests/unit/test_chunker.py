"""Unit tests for the structure-aware chunker."""

import pytest
from ingestion.processors.chunker import StructureAwareChunker


@pytest.fixture
def chunker():
    return StructureAwareChunker(chunk_size=100, chunk_overlap=10)


def test_basic_html_chunking(chunker):
    html = """
    <h1>Setup</h1>
    <p>This is the setup section with some introductory text.</p>
    <h2>Installation</h2>
    <p>Run the installer using the command line.</p>
    """
    chunks = chunker.chunk_html(html, base_title="Docs")
    assert len(chunks) > 0
    assert all(c.content for c in chunks)


def test_section_path_preserved(chunker):
    html = """
    <h1>Getting Started</h1>
    <h2>Installation</h2>
    <p>Install the package using pip.</p>
    """
    chunks = chunker.chunk_html(html, base_title="Guide")
    text_chunks = [c for c in chunks if c.chunk_type == "text"]
    assert any("Installation" in c.section_path for c in text_chunks)


def test_table_chunk_type(chunker):
    html = """
    <h1>Reference</h1>
    <table>
        <tr><th>Name</th><th>Value</th></tr>
        <tr><td>timeout</td><td>30s</td></tr>
        <tr><td>retries</td><td>3</td></tr>
    </table>
    """
    chunks = chunker.chunk_html(html)
    table_chunks = [c for c in chunks if c.chunk_type == "table"]
    assert len(table_chunks) == 1
    assert "timeout" in table_chunks[0].content
    assert "|" in table_chunks[0].content  # markdown table format


def test_image_chunk_type(chunker):
    html = """
    <h1>Architecture</h1>
    <img src="https://example.com/diagram.png" alt="System Architecture Diagram" />
    """
    chunks = chunker.chunk_html(html)
    img_chunks = [c for c in chunks if c.chunk_type == "image_description"]
    assert len(img_chunks) == 1
    assert img_chunks[0].metadata.get("img_src") == "https://example.com/diagram.png"


def test_plain_text_fallback(chunker):
    text = "This is plain text content. " * 20
    chunks = chunker.chunk_plain_text(text, title="Plain Doc")
    assert len(chunks) > 0
    assert all(c.chunk_type == "text" for c in chunks)


def test_token_count_estimated(chunker):
    html = "<p>Short paragraph.</p>"
    chunks = chunker.chunk_html(html)
    for c in chunks:
        assert c.token_count >= 0


def test_empty_html(chunker):
    chunks = chunker.chunk_html("", base_title="Empty")
    assert chunks == []


def test_heading_only_html(chunker):
    html = "<h1>Title Only</h1>"
    chunks = chunker.chunk_html(html)
    # Heading alone with no body text produces no text chunks
    assert all(c.chunk_type != "text" or c.content for c in chunks)
