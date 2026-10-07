"""
ACL bleed test against a real PostgreSQL + pgvector instance.

Skipped unless TEST_DATABASE_URL is set, e.g.
  TEST_DATABASE_URL=postgresql+asyncpg://postgres:pw@localhost/ragtest pytest tests/integration/test_acl_postgres.py
Uses its own throwaway table; it does not touch document_chunks.
"""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from core.config import settings
from retrieval import hybrid_retriever as hr

DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")

DIM = settings.EMBEDDING_DIM


@pytest.fixture
async def db():
    engine = create_async_engine(DB_URL)
    vec = "[" + ",".join(["0.1"] * DIM) + "]"
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("DROP TABLE IF EXISTS document_chunks"))
        await conn.execute(text(f"""
            CREATE TABLE document_chunks (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                source_id uuid DEFAULT gen_random_uuid(),
                content text, section_path text, heading text, chunk_type text,
                acl_groups json DEFAULT '[]', metadata json DEFAULT '{{}}',
                embedding vector({DIM}))
        """))
        for content, acl in [
            ("public sso guide", "[]"),
            ("engineering sso runbook", '["engineering"]'),
            ("hr sso salary data", '["hr"]'),
        ]:
            await conn.execute(
                text("INSERT INTO document_chunks (content, section_path, heading, chunk_type, acl_groups, embedding) "
                     "VALUES (:c, 's', 'h', 'text', CAST(:a AS json), CAST(:v AS vector))"),
                {"c": content, "a": acl, "v": vec},
            )
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


async def _visible(db, groups):
    vec = await hr._vector_search(db, [0.1] * DIM, 10, groups)
    fts = await hr._bm25_search(db, "sso", 10, groups)
    return {c.content for c in vec}, {c.content for c in fts}


@pytest.mark.asyncio
@pytest.mark.parametrize("groups", [None, []])
async def test_no_groups_sees_only_public(db, groups):
    vec, fts = await _visible(db, groups)
    assert vec == fts == {"public sso guide"}


@pytest.mark.asyncio
async def test_group_member_sees_own_and_public_but_not_other_groups(db):
    vec, fts = await _visible(db, ["engineering"])
    assert vec == fts == {"public sso guide", "engineering sso runbook"}
    assert "hr sso salary data" not in vec | fts
