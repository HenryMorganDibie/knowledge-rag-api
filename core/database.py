"""
Async SQLAlchemy engine + session factory.
On startup, ensures pgvector extension and all tables exist.
HNSW indexes are created here for ANN search performance.
"""

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text

from core.config import settings
from core.logger import get_logger

logger = get_logger(__name__)

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    """
    Bootstrap: enable pgvector, create tables, build HNSW indexes.
    Safe to run on every startup (all DDL is idempotent).
    """
    async with engine.begin() as conn:
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
