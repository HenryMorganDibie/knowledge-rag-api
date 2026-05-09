"""Retrieval API — hybrid vector + BM25 search with ACL filtering."""

from typing import List, Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.database import get_db
from retrieval.hybrid_retriever import retrieve

router = APIRouter()


class RetrievalRequest(BaseModel):
    query: str
    acl_groups: Optional[List[str]] = None
    top_k: int = 5
    diagnostics: bool = False


@router.post("/")
async def retrieval_endpoint(request: RetrievalRequest, db=Depends(get_db)):
    """
    Hybrid retrieval: vector similarity + BM25 full-text, merged via RRF,
    reranked by cross-encoder. ACL groups filter chunks the caller can't see.
    """
    result = await retrieve(
        db=db,
        query=request.query,
        acl_groups=request.acl_groups,
        top_k=request.top_k,
        diagnostics=request.diagnostics,
    )
    return result
