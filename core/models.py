"""
ORM models for the canonical document registry, chunk store,
feedback log, and ingestion audit trail.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Text, Integer, Float, Boolean,
    DateTime, JSON, ForeignKey, Enum as SAEnum
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector

from core.database import Base
from core.config import settings


class DocumentSource(Base):
    """Canonical document registry — one row per unique source document."""
    __tablename__ = "document_sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_type = Column(SAEnum("document360", "sharepoint", name="source_type_enum"), nullable=False)
    external_id = Column(String(512), nullable=False, unique=True)   # e.g. D360 article ID
    title = Column(Text, nullable=False)
    url = Column(Text)
    acl_groups = Column(JSON, default=list)          # ["group_a", "group_b"]
    content_fingerprint = Column(String(64))         # SHA-256 of raw content
    last_ingested_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    metadata_ = Column("metadata", JSON, default=dict)

    revisions = relationship("DocumentRevision", back_populates="source", cascade="all, delete-orphan")
    chunks = relationship("DocumentChunk", back_populates="source", cascade="all, delete-orphan")


class DocumentRevision(Base):
    """Immutable revision history — every re-ingestion appends a row."""
    __tablename__ = "document_revisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id = Column(UUID(as_uuid=True), ForeignKey("document_sources.id"), nullable=False)
    revision_number = Column(Integer, nullable=False)
    content_fingerprint = Column(String(64), nullable=False)
    ingested_at = Column(DateTime, default=datetime.utcnow)
    change_summary = Column(Text)
    s3_raw_key = Column(Text)       # S3 key for raw source file snapshot
    chunk_count = Column(Integer, default=0)

    source = relationship("DocumentSource", back_populates="revisions")


class DocumentChunk(Base):
    """
    Atomic chunk unit — one row per chunk per published revision.
    Old chunks from prior revisions are deleted atomically on republish.
    """
    __tablename__ = "document_chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id = Column(UUID(as_uuid=True), ForeignKey("document_sources.id"), nullable=False)
    revision_id = Column(UUID(as_uuid=True), ForeignKey("document_revisions.id"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    section_path = Column(Text)          # e.g. "Setup > Installation > Windows"
    heading = Column(Text)
    chunk_type = Column(
        SAEnum("text", "table", "image_description", name="chunk_type_enum"),
        default="text"
    )
    token_count = Column(Integer)
    embedding = Column(Vector(settings.EMBEDDING_DIM))
    s3_artifact_key = Column(Text)       # S3 key for chunk JSON artifact
    acl_groups = Column(JSON, default=list)
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)

    source = relationship("DocumentSource", back_populates="chunks")


class FeedbackLog(Base):
    """Thumbs up/down + failure category capture per query."""
    __tablename__ = "feedback_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query = Column(Text, nullable=False)
    answer = Column(Text)
    rating = Column(SAEnum("positive", "negative", name="rating_enum"), nullable=False)
    failure_category = Column(
        SAEnum(
            "wrong_answer", "missing_citation", "stale_content",
            "irrelevant_chunks", "other",
            name="failure_category_enum"
        ),
        nullable=True
    )
    comment = Column(Text)
    chunk_ids = Column(JSON, default=list)   # chunk IDs surfaced in this answer
    user_id = Column(String(256))
    created_at = Column(DateTime, default=datetime.utcnow)


class IngestionJob(Base):
    """Audit trail for every ingestion run."""
    __tablename__ = "ingestion_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_type = Column(String(64))
    status = Column(
        SAEnum("pending", "running", "completed", "failed", name="job_status_enum"),
        default="pending"
    )
    documents_scanned = Column(Integer, default=0)
    documents_changed = Column(Integer, default=0)
    documents_skipped = Column(Integer, default=0)
    chunks_created = Column(Integer, default=0)
    error_detail = Column(Text)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
