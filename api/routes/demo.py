"""
Demo-mode API — public, credential-free access to a bundled fictional
knowledge base. Every response carries `"mode": "demo"`; these endpoints
never call external services and never fall back from a live request.
"""

from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from core.config import settings
from core.security import rate_limit
from demo import engine

router = APIRouter()

CollectionId = Literal["lumen-developer-docs", "lumen-it-operations"]
RetrievalMode = Literal["hybrid", "vector", "keyword"]


def _require_demo_enabled() -> None:
    if not settings.DEMO_MODE_ENABLED:
        raise HTTPException(status_code=404, detail="Demo mode is disabled on this deployment.")


class DemoQueryRequest(BaseModel):
    collection_id: CollectionId = Field(description="Sample collection to query.")
    query: str = Field(min_length=1, max_length=500, description="Natural-language question.")
    top_k: int = Field(default=5, ge=1, le=10)
    retrieval_mode: RetrievalMode = Field(
        default="hybrid", description="hybrid = vector + keyword merged with RRF; or a single leg for comparison.")

    @field_validator("query")
    @classmethod
    def _strip_query(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query must not be blank")
        return v


class DemoIngestRequest(BaseModel):
    collection_id: CollectionId
    scenario: Literal["unchanged", "upstream_edit", "auth_failure"] = Field(
        default="upstream_edit",
        description="unchanged: re-sync identical sources (all skipped). upstream_edit: one source document "
                    "changed upstream. auth_failure: the connector's credentials are rejected.",
    )


class DemoDocumentSummary(BaseModel):
    id: str
    title: str
    category_path: str
    updated_at: str
    fingerprint: str
    chunk_count: int


class DemoCollection(BaseModel):
    id: str
    name: str
    source_type: str
    description: str
    simulated_origin: str
    sample_questions: List[str]
    documents: List[DemoDocumentSummary]
    restricted_documents_hidden: int
    chunk_count: int


class DemoCollectionsResponse(BaseModel):
    mode: Literal["demo"] = "demo"
    notice: str
    indexed_at: str
    collections: List[DemoCollection]


class DemoChunkView(BaseModel):
    chunk_id: str
    chunk_index: int
    chunk_type: str
    section_path: str
    heading: str
    token_count: int
    content: str


class DemoDocumentResponse(BaseModel):
    mode: Literal["demo"] = "demo"
    id: str
    collection_id: str
    title: str
    source_type: str
    category_path: str
    updated_at: str
    fingerprint: str
    acl_groups: List[str]
    chunks: List[DemoChunkView]


class DemoCitation(BaseModel):
    index: int
    chunk_id: str
    document_id: str
    document_title: str
    source_type: str
    section_path: str
    heading: str
    excerpt: str


class DemoRetrievedChunk(BaseModel):
    chunk_id: str
    source_id: str
    content: str
    section_path: str
    heading: str
    chunk_type: str
    vector_score: Optional[float]
    rrf_score: Optional[float]
    rerank_score: Optional[float]
    document_title: str
    category_path: str
    source_type: str
    vector_rank: Optional[int]
    bm25_rank: Optional[int]
    bm25_score: Optional[float]


class DemoRetrievalResponse(BaseModel):
    mode: Literal["demo"]
    retrieval_mode: RetrievalMode
    collection_id: str
    query: str
    chunks: List[DemoRetrievedChunk]
    total_retrieved: int
    diagnostics: dict


class DemoAnswerResponse(BaseModel):
    mode: Literal["demo"]
    answer: str
    grounded: bool = Field(description="False when no retrieved passage supports an answer.")
    citations: List[DemoCitation]
    chunks_used: int
    model: str
    retrieval: DemoRetrievalResponse
    latency_ms: float


@router.get("/collections", response_model=DemoCollectionsResponse, dependencies=[Depends(_require_demo_enabled)])
async def list_collections():
    """Sample collections, their public documents, and real chunk counts and fingerprints."""
    store = engine.get_demo_store()
    out = []
    for c in store.collections.values():
        public = [d for d in c.documents.values() if d.is_public]
        out.append(DemoCollection(
            id=c.id, name=c.name, source_type=c.source_type, description=c.description,
            simulated_origin=c.simulated_origin, sample_questions=c.sample_questions,
            documents=[DemoDocumentSummary(id=d.id, title=d.title, category_path=d.category_path,
                                           updated_at=d.updated_at, fingerprint=d.fingerprint,
                                           chunk_count=len(d.chunk_ids)) for d in public],
            restricted_documents_hidden=len(c.documents) - len(public),
            chunk_count=sum(len(d.chunk_ids) for d in public),
        ))
    return DemoCollectionsResponse(notice=store.notice, indexed_at=store.built_at, collections=out)


@router.get("/documents/{document_id}", response_model=DemoDocumentResponse,
            dependencies=[Depends(_require_demo_enabled)])
async def get_document(document_id: str):
    """Open a cited source document with its chunks. Restricted documents return 404 (no existence leak)."""
    store = engine.get_demo_store()
    doc = store.document(document_id)
    if doc is None or not doc.is_public:
        raise HTTPException(status_code=404, detail="Document not found.")
    coll = next(c for c in store.collections.values() if document_id in c.documents)
    chunks = [c for c in coll.chunks if c.document_id == document_id]
    return DemoDocumentResponse(
        id=doc.id, collection_id=coll.id, title=doc.title, source_type=doc.source_type,
        category_path=doc.category_path, updated_at=doc.updated_at, fingerprint=doc.fingerprint,
        acl_groups=doc.acl_groups,
        chunks=[DemoChunkView(chunk_id=c.chunk_id, chunk_index=c.chunk_index, chunk_type=c.chunk_type,
                              section_path=c.section_path, heading=c.heading, token_count=c.token_count,
                              content=c.content) for c in chunks],
    )


@router.post("/retrieve", response_model=DemoRetrievalResponse,
             dependencies=[Depends(_require_demo_enabled),
                           Depends(rate_limit("query", "RATE_LIMIT_QUERY_PER_MINUTE"))])
async def demo_retrieve(request: DemoQueryRequest):
    """Hybrid retrieval over the sample collection with a full scoring trace. Anonymous: public chunks only."""
    return engine.retrieve(request.collection_id, request.query, request.top_k, request.retrieval_mode)


@router.post("/ask", response_model=DemoAnswerResponse,
             dependencies=[Depends(_require_demo_enabled),
                           Depends(rate_limit("query", "RATE_LIMIT_QUERY_PER_MINUTE"))])
async def demo_ask(request: DemoQueryRequest):
    """Extractive grounded answer with numbered citations. Deterministic; no LLM call."""
    return engine.answer(request.collection_id, request.query, request.top_k, request.retrieval_mode)


@router.post("/ingest", dependencies=[Depends(_require_demo_enabled),
                                      Depends(rate_limit("ingest", "RATE_LIMIT_INGEST_PER_MINUTE"))])
async def demo_ingest(request: DemoIngestRequest):
    """
    Sandboxed ingestion run over an allow-listed sample source. Only the
    bundled collections can be targeted; no URLs or external hosts are accepted.
    """
    return engine.run_sync(request.collection_id, request.scenario)
