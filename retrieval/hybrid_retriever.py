"""
Hybrid retrieval engine.

Pipeline:
1. Vector search — cosine similarity via pgvector HNSW index
2. BM25 full-text search — PostgreSQL tsvector / tsquery
3. Reciprocal Rank Fusion (RRF) — merge ranked lists
4. ACL filtering — remove chunks the requesting user can't see
5. Cross-encoder reranking — sentence-transformers for final ordering
6. Return top-K with diagnostics metadata
"""

from typing import List, Optional
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sentence_transformers import CrossEncoder

from core.config import settings
from core.logger import get_logger
from ingestion.processors.embedder import embed_query

logger = get_logger(__name__)

_reranker: CrossEncoder | None = None


def _get_reranker() -> CrossEncoder:
    global _reranker
    if _reranker is None:
        _reranker = CrossEncoder(settings.RERANKER_MODEL)
    return _reranker


@dataclass
class RetrievedChunk:
    chunk_id: str
    source_id: str
    content: str
    section_path: str
    heading: str
    chunk_type: str
    acl_groups: list
    vector_score: Optional[float] = None
    bm25_rank: Optional[int] = None
    rrf_score: float = 0.0
    rerank_score: Optional[float] = None
    metadata: dict = field(default_factory=dict)


async def retrieve(
    db: AsyncSession,
    query: str,
    acl_groups: Optional[List[str]] = None,
    top_k: int = None,
    vector_top_k: int = None,
    bm25_top_k: int = None,
    diagnostics: bool = False,
) -> dict:
    """
    Main retrieval entry point.
    Returns top-K reranked chunks with optional diagnostics.
    """
    top_k = top_k or settings.RETRIEVAL_FINAL_TOP_K
    vector_top_k = vector_top_k or settings.RETRIEVAL_VECTOR_TOP_K
    bm25_top_k = bm25_top_k or settings.RETRIEVAL_BM25_TOP_K

    # 1. Embed query
    query_embedding = await embed_query(query)

    # 2. Vector search
    vector_hits = await _vector_search(db, query_embedding, vector_top_k, acl_groups)

    # 3. BM25 full-text search
    bm25_hits = await _bm25_search(db, query, bm25_top_k, acl_groups)

    # 4. RRF merge
    merged = _reciprocal_rank_fusion(vector_hits, bm25_hits)

    # 5. Rerank top candidates
    candidates = merged[:top_k * 3]  # rerank a larger pool
    reranked = _rerank(query, candidates)

    final_chunks = reranked[:top_k]

    result = {
        "query": query,
        "chunks": [_chunk_to_dict(c) for c in final_chunks],
        "total_retrieved": len(merged),
    }

    if diagnostics:
        result["diagnostics"] = {
            "vector_hits": len(vector_hits),
            "bm25_hits": len(bm25_hits),
            "after_rrf": len(merged),
            "after_rerank": len(reranked),
            "acl_filter_applied": acl_groups is not None,
            "top_rrf_scores": [
                {"chunk_id": c.chunk_id, "rrf_score": round(c.rrf_score, 4)}
                for c in merged[:10]
            ],
        }

    return result


async def _vector_search(
    db: AsyncSession,
    embedding: List[float],
    top_k: int,
    acl_groups: Optional[List[str]],
) -> List[RetrievedChunk]:
    """Cosine similarity search via pgvector HNSW index."""
    embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

    acl_clause = ""
    params: dict = {"embedding": embedding_str, "top_k": top_k}

    if acl_groups:
        acl_clause = "AND (acl_groups = '[]'::jsonb OR acl_groups ?| :acl_groups)"
        params["acl_groups"] = acl_groups

    sql = text(f"""
        SELECT
            id::text AS chunk_id,
            source_id::text,
            content,
            section_path,
            heading,
            chunk_type,
            acl_groups,
            metadata,
            1 - (embedding <=> :embedding::vector) AS vector_score
        FROM document_chunks
        WHERE 1=1 {acl_clause}
        ORDER BY embedding <=> :embedding::vector
        LIMIT :top_k
    """)

    result = await db.execute(sql, params)
    rows = result.mappings().all()

    return [
        RetrievedChunk(
            chunk_id=row["chunk_id"],
            source_id=row["source_id"],
            content=row["content"],
            section_path=row["section_path"] or "",
            heading=row["heading"] or "",
            chunk_type=row["chunk_type"],
            acl_groups=row["acl_groups"] or [],
            vector_score=float(row["vector_score"]),
            metadata=row["metadata"] or {},
        )
        for row in rows
    ]


async def _bm25_search(
    db: AsyncSession,
    query: str,
    top_k: int,
    acl_groups: Optional[List[str]],
) -> List[RetrievedChunk]:
    """Full-text BM25-style search using PostgreSQL tsvector + ts_rank."""
    acl_clause = ""
    params: dict = {"query": query, "top_k": top_k}

    if acl_groups:
        acl_clause = "AND (acl_groups = '[]'::jsonb OR acl_groups ?| :acl_groups)"
        params["acl_groups"] = acl_groups

    sql = text(f"""
        SELECT
            id::text AS chunk_id,
            source_id::text,
            content,
            section_path,
            heading,
            chunk_type,
            acl_groups,
            metadata,
            ts_rank(to_tsvector('english', content), plainto_tsquery('english', :query)) AS bm25_score
        FROM document_chunks
        WHERE to_tsvector('english', content) @@ plainto_tsquery('english', :query)
        {acl_clause}
        ORDER BY bm25_score DESC
        LIMIT :top_k
    """)

    result = await db.execute(sql, params)
    rows = result.mappings().all()

    return [
        RetrievedChunk(
            chunk_id=row["chunk_id"],
            source_id=row["source_id"],
            content=row["content"],
            section_path=row["section_path"] or "",
            heading=row["heading"] or "",
            chunk_type=row["chunk_type"],
            acl_groups=row["acl_groups"] or [],
            metadata=row["metadata"] or {},
        )
        for row in rows
    ]


def _reciprocal_rank_fusion(
    vector_hits: List[RetrievedChunk],
    bm25_hits: List[RetrievedChunk],
    k: int = 60,
) -> List[RetrievedChunk]:
    """
    Merge two ranked lists using Reciprocal Rank Fusion.
    RRF score = sum of 1/(k + rank) across lists.
    """
    scores: dict[str, float] = {}
    chunk_map: dict[str, RetrievedChunk] = {}

    for rank, chunk in enumerate(vector_hits, 1):
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0) + 1 / (k + rank)
        chunk_map[chunk.chunk_id] = chunk

    for rank, chunk in enumerate(bm25_hits, 1):
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0) + 1 / (k + rank)
        if chunk.chunk_id not in chunk_map:
            chunk_map[chunk.chunk_id] = chunk
        chunk_map[chunk.chunk_id].bm25_rank = rank

    for chunk_id, score in scores.items():
        chunk_map[chunk_id].rrf_score = score

    return sorted(chunk_map.values(), key=lambda c: c.rrf_score, reverse=True)


def _rerank(query: str, chunks: List[RetrievedChunk]) -> List[RetrievedChunk]:
    """Cross-encoder reranking for final precision boost."""
    if not chunks:
        return chunks
    reranker = _get_reranker()
    pairs = [(query, c.content) for c in chunks]
    scores = reranker.predict(pairs)
    for chunk, score in zip(chunks, scores):
        chunk.rerank_score = float(score)
    return sorted(chunks, key=lambda c: c.rerank_score, reverse=True)


def _chunk_to_dict(chunk: RetrievedChunk) -> dict:
    return {
        "chunk_id": chunk.chunk_id,
        "source_id": chunk.source_id,
        "content": chunk.content,
        "section_path": chunk.section_path,
        "heading": chunk.heading,
        "chunk_type": chunk.chunk_type,
        "vector_score": chunk.vector_score,
        "rrf_score": round(chunk.rrf_score, 4),
        "rerank_score": round(chunk.rerank_score, 4) if chunk.rerank_score else None,
    }
