"""
Integration tests for the ingestion pipeline.
Uses an in-memory SQLite-compatible mock to test pipeline logic
without requiring a live PostgreSQL instance.
"""

import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from ingestion.pipeline import ingest_document


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.fixture
def sample_html():
    return """
    <h1>API Reference</h1>
    <p>This section covers the REST API endpoints available in the system.</p>
    <h2>Authentication</h2>
    <p>All requests must include a Bearer token in the Authorization header.</p>
    <table>
        <tr><th>Header</th><th>Value</th></tr>
        <tr><td>Authorization</td><td>Bearer {token}</td></tr>
    </table>
    """


@pytest.mark.asyncio
async def test_ingest_new_document_calls_embed(mock_db, sample_html):
    """New documents should be embedded and committed."""
    with patch("ingestion.pipeline.embed_texts", new_callable=AsyncMock) as mock_embed, \
         patch("ingestion.pipeline.upload_raw_document", return_value="s3://raw/key"), \
         patch("ingestion.pipeline.upload_chunk_artifact", return_value="s3://chunk/key"), \
         patch("ingestion.pipeline.describe_image_from_url", new_callable=AsyncMock):

        mock_embed.return_value = [[0.1] * 1536, [0.2] * 1536, [0.3] * 1536]

        result = await ingest_document(
            db=mock_db,
            source_type="document360",
            external_id="article-001",
            title="API Reference",
            html_content=sample_html,
            raw_bytes=sample_html.encode(),
            url="https://docs.example.com/api",
            category_path="Docs > API Reference",
            acl_groups=["engineering"],
            fingerprint="abc123fingerprint",
            job_id=uuid.uuid4(),
        )

    assert result["action"] == "ingested"
    assert result["chunks"] > 0
    mock_embed.assert_called_once()
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_ingest_unchanged_document_skips(mock_db, sample_html):
    """Documents with matching fingerprint should be skipped."""
    existing_source = MagicMock()
    existing_source.content_fingerprint = "same-fingerprint"
    existing_source.id = uuid.uuid4()

    mock_db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=existing_source))
    )

    result = await ingest_document(
        db=mock_db,
        source_type="document360",
        external_id="article-002",
        title="Unchanged Article",
        html_content=sample_html,
        raw_bytes=sample_html.encode(),
        url="https://docs.example.com/unchanged",
        category_path="Docs > Unchanged",
        acl_groups=[],
        fingerprint="same-fingerprint",
        job_id=uuid.uuid4(),
    )

    assert result["action"] == "skipped"
    assert result["chunks"] == 0
    mock_db.commit.assert_not_called()
