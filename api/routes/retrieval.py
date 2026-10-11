"""Retrieval API — hybrid vector + BM25 search with ACL filtering."""

from fastapi import APIRouter, Depends, Request

from core.database import get_db
from core.schemas import LiveQueryBase
from core.security import rate_limit, require_live_query_access, resolve_acl_groups
from retrieval.hybrid_retriever import retrieve

router = APIRouter()


class RetrievalRequest(LiveQueryBase):
    diagnostics: bool = False


@router.post("/", dependencies=[Depends(require_live_query_access),
                                Depends(rate_limit("query", "RATE_LIMIT_QUERY_PER_MINUTE"))])
async def retrieval_endpoint(request: RetrievalRequest, http_request: Request, db=Depends(get_db)):
    """
    Hybrid retrieval: vector similarity + BM25 full-text, merged via RRF,
    reranked by cross-encoder. ACL groups filter chunks the caller can't see;
    groups are taken from the request only for authenticated service callers.
    """
    result = await retrieve(
        db=db,
        query=request.query,
        acl_groups=resolve_acl_groups(http_request, request.acl_groups),
        top_k=request.top_k,
        diagnostics=request.diagnostics,
    )
    return result
