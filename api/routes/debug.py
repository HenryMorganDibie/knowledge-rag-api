"""
Debug endpoint — full trace of retrieval and answer generation.
Shows vector scores, BM25 ranks, RRF merge, rerank scores,
and the exact prompt sent to the LLM.
"""

from typing import List, Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.database import get_db
from retrieval.hybrid_retriever import retrieve

router = APIRouter()


class DebugRequest(BaseModel):
    query: str
    acl_groups: Optional[List[str]] = None
    top_k: int = 5


@router.post("/trace")
async def debug_trace(request: DebugRequest, db=Depends(get_db)):
    """
    Full retrieval trace with all intermediate scores.
    Use this to diagnose chunk quality, ACL issues, and ranking problems.
    """
    result = await retrieve(
        db=db,
        query=request.query,
        acl_groups=request.acl_groups,
        top_k=request.top_k,
        diagnostics=True,
    )
    return {
        "query": request.query,
        "acl_groups_applied": request.acl_groups,
        "retrieval_result": result,
    }
