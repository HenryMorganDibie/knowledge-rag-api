"""Unit tests for Reciprocal Rank Fusion merge logic."""

import pytest
from retrieval.hybrid_retriever import _reciprocal_rank_fusion, RetrievedChunk


def _make_chunk(chunk_id: str, vector_score: float = None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        source_id="src-1",
        content=f"Content for {chunk_id}",
        section_path="Section > Sub",
        heading="Sub",
        chunk_type="text",
        acl_groups=[],
        vector_score=vector_score,
    )


def test_rrf_deduplicates_shared_chunks():
    vector_hits = [_make_chunk("a"), _make_chunk("b"), _make_chunk("c")]
    bm25_hits = [_make_chunk("b"), _make_chunk("c"), _make_chunk("d")]
    merged = _reciprocal_rank_fusion(vector_hits, bm25_hits)
    ids = [c.chunk_id for c in merged]
    assert len(ids) == len(set(ids))  # no duplicates


def test_rrf_boosts_chunks_in_both_lists():
    vector_hits = [_make_chunk("shared"), _make_chunk("vector_only")]
    bm25_hits = [_make_chunk("shared"), _make_chunk("bm25_only")]
    merged = _reciprocal_rank_fusion(vector_hits, bm25_hits)
    # "shared" should rank highest — it appears in both lists
    assert merged[0].chunk_id == "shared"


def test_rrf_returns_all_unique_chunks():
    vector_hits = [_make_chunk("a"), _make_chunk("b")]
    bm25_hits = [_make_chunk("c"), _make_chunk("d")]
    merged = _reciprocal_rank_fusion(vector_hits, bm25_hits)
    assert len(merged) == 4


def test_rrf_scores_are_positive():
    vector_hits = [_make_chunk("x")]
    bm25_hits = [_make_chunk("y")]
    merged = _reciprocal_rank_fusion(vector_hits, bm25_hits)
    assert all(c.rrf_score > 0 for c in merged)


def test_rrf_empty_inputs():
    merged = _reciprocal_rank_fusion([], [])
    assert merged == []


def test_rrf_one_empty_list():
    vector_hits = [_make_chunk("a"), _make_chunk("b")]
    merged = _reciprocal_rank_fusion(vector_hits, [])
    assert len(merged) == 2
