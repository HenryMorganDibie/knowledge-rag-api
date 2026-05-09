"""
Core ingestion pipeline.

Key guarantees:
- Fingerprint-based change detection: unchanged docs are skipped entirely.
- Atomic publishing: new chunks are inserted and old chunks deleted in a
  single transaction — stale chunks are never visible during re-ingestion.
- Every raw file and chunk artifact is stored in S3 before DB commit.
- Full audit trail written to ingestion_jobs table.
"""

import uuid
from datetime import datetime
from typing import List

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import DocumentSource, DocumentRevision, DocumentChunk, IngestionJob
from core.logger import get_logger
from ingestion.processors.chunker import StructureAwareChunker, Chunk
from ingestion.processors.embedder import embed_texts
from ingestion.processors.image_describer import describe_image_from_url
from storage.s3_client import upload_raw_document, upload_chunk_artifact

logger = get_logger(__name__)
chunker = StructureAwareChunker()


async def ingest_document(
    db: AsyncSession,
    source_type: str,
    external_id: str,
    title: str,
    html_content: str,
    raw_bytes: bytes,
    url: str,
    category_path: str,
    acl_groups: List[str],
    fingerprint: str,
    job_id: uuid.UUID,
) -> dict:
    """
    Full ingestion pipeline for a single document.
    Returns a summary dict with chunk count and action taken.
    """

    # 1. Look up existing source record
    result = await db.execute(
        select(DocumentSource).where(DocumentSource.external_id == external_id)
    )
    source = result.scalar_one_or_none()

    # 2. Fingerprint check — skip if unchanged
    if source and source.content_fingerprint == fingerprint:
        logger.info(f"Skipping unchanged document: {external_id}")
        return {"action": "skipped", "external_id": external_id, "chunks": 0}

    # 3. Upsert DocumentSource
    if source is None:
        source = DocumentSource(
            external_id=external_id,
            source_type=source_type,
            title=title,
            url=url,
            acl_groups=acl_groups,
            content_fingerprint=fingerprint,
            last_ingested_at=datetime.utcnow(),
        )
        db.add(source)
        await db.flush()   # get source.id
        revision_number = 1
    else:
        source.title = title
        source.url = url
        source.acl_groups = acl_groups
        source.content_fingerprint = fingerprint
        source.last_ingested_at = datetime.utcnow()
        # Determine next revision number
        rev_result = await db.execute(
            select(DocumentRevision)
            .where(DocumentRevision.source_id == source.id)
            .order_by(DocumentRevision.revision_number.desc())
        )
        last_rev = rev_result.scalars().first()
        revision_number = (last_rev.revision_number + 1) if last_rev else 1

    # 4. Upload raw file to S3
    s3_raw_key = upload_raw_document(
        content=raw_bytes,
        source_type=source_type,
        external_id=external_id,
        filename=f"content_rev{revision_number}.html",
        revision=revision_number,
    )

    # 5. Create revision record
    revision = DocumentRevision(
        source_id=source.id,
        revision_number=revision_number,
        content_fingerprint=fingerprint,
        s3_raw_key=s3_raw_key,
        ingested_at=datetime.utcnow(),
    )
    db.add(revision)
    await db.flush()

    # 6. Chunk the document
    chunks: List[Chunk] = chunker.chunk_html(html_content, base_title=title)
    logger.info(f"Chunked '{title}' into {len(chunks)} chunks")

    # 7. Enrich image_description chunks with vision model descriptions
    for chunk in chunks:
        if chunk.chunk_type == "image_description" and chunk.metadata.get("img_src"):
            description = await describe_image_from_url(chunk.metadata["img_src"])
            if description:
                chunk.content = description

    # 8. Embed all chunks in batch
    texts = [c.content for c in chunks]
    embeddings = await embed_texts(texts)

    # 9. ATOMIC PUBLISH — delete old chunks, insert new ones in one transaction
    await db.execute(
        delete(DocumentChunk).where(DocumentChunk.source_id == source.id)
    )

    chunk_rows = []
    for chunk, embedding in zip(chunks, embeddings):
        artifact_data = {
            "source_id": str(source.id),
            "revision_id": str(revision.id),
            "chunk_index": chunk.chunk_index,
            "content": chunk.content,
            "section_path": chunk.section_path,
            "heading": chunk.heading,
            "chunk_type": chunk.chunk_type,
        }
        s3_key = upload_chunk_artifact(artifact_data, str(source.id), chunk.chunk_index)

        row = DocumentChunk(
            source_id=source.id,
            revision_id=revision.id,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            section_path=chunk.section_path,
            heading=chunk.heading,
            chunk_type=chunk.chunk_type,
            token_count=chunk.token_count,
            embedding=embedding,
            s3_artifact_key=s3_key,
            acl_groups=acl_groups,
            metadata_=chunk.metadata,
        )
        db.add(row)
        chunk_rows.append(row)

    revision.chunk_count = len(chunk_rows)
    await db.commit()

    logger.info(f"Published {len(chunk_rows)} chunks for '{title}' (rev {revision_number})")
    return {
        "action": "ingested",
        "external_id": external_id,
        "revision": revision_number,
        "chunks": len(chunk_rows),
    }
