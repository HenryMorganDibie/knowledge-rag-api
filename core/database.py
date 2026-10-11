"""
Async SQLAlchemy engine + session factory.
On startup, ensures pgvector extension and all tables exist.
HNSW indexes are created here for ANN search performance.

The engine is created lazily so the API (and its demo mode) can start
without a database; live endpoints then return 503 instead of crashing.
"""

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text

from core.config import settings
from core.logger import get_logger

logger = get_logger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker | None = None


class Base(DeclarativeBase):
    pass


def database_configured() -> bool:
    return bool(settings.DATABASE_URL.strip())


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        if not database_configured():
            raise RuntimeError("DATABASE_URL is not configured")
        _engine = create_async_engine(
            settings.DATABASE_URL,
            pool_size=settings.DATABASE_POOL_SIZE,
            max_overflow=settings.DATABASE_MAX_OVERFLOW,
            pool_pre_ping=True,
            echo=False,
        )
        _sessionmaker = async_sessionmaker(bind=_engine, class_=AsyncSession, expire_on_commit=False)
    return _engine


def AsyncSessionLocal() -> AsyncSession:
    """Open a new session (used by background jobs that outlive a request)."""
    get_engine()
    return _sessionmaker()


async def get_db() -> AsyncSession:
    if not database_configured():
        raise HTTPException(
            status_code=503,
            detail="Live knowledge base unavailable: DATABASE_URL is not configured. "
                   "Use the /demo endpoints for the bundled sample knowledge base.",
        )
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    """
    Bootstrap: enable pgvector, create tables, build HNSW indexes.
    Safe to run on every startup (all DDL is idempotent).
    """
    async with get_engine().begin() as conn:
        # Enable pgvector
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))

        # Import all models so Base knows about them
        from core import models  # noqa: F401

        await conn.run_sync(Base.metadata.create_all)

        # HNSW index for cosine similarity (ANN search)
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
            ON document_chunks
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """))

        # GIN index for full-text search
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_chunks_fts
            ON document_chunks
            USING gin(to_tsvector('english', content))
        """))

    logger.info("Database initialised — pgvector, tables, and indexes ready.")
