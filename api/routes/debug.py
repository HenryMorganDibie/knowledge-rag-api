"""
Debug endpoint — full trace of retrieval and answer generation.
Shows vector scores, BM25 ranks, RRF merge, rerank scores,
and the exact prompt sent to the LLM.
"""

from fastapi import APIRouter, Depends

from core.database import get_db
from core.schemas import LiveQueryBase
from core.security import rate_limit, require_service_key
from retrieval.hybrid_retriever import retrieve

router = APIRouter()


class DebugRequest(LiveQueryBase):
    pass


@router.post("/trace", dependencies=[Depends(require_service_key),
                                     Depends(rate_limit("query", "RATE_LIMIT_QUERY_PER_MINUTE"))])
async def debug_trace(request: DebugRequest, db=Depends(get_db)):
    """
    Full retrieval trace with all intermediate scores. Requires X-API-Key:
    diagnostics expose internals, and the caller is trusted to assert acl_groups.
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
        "acl_groups_applied": request.acl_groups or [],
        "retrieval_result": result,
    }
