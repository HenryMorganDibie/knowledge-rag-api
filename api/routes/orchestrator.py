"""Orchestrator API — grounded answer generation with citation blocks."""

from fastapi import APIRouter, Depends, Request

from core.database import get_db
from core.schemas import LiveQueryBase
from core.security import rate_limit, require_live_query_access, resolve_acl_groups
from orchestrator.answer_engine import answer

router = APIRouter()


class AskRequest(LiveQueryBase):
    pass


@router.post("/", dependencies=[Depends(require_live_query_access),
                                Depends(rate_limit("query", "RATE_LIMIT_QUERY_PER_MINUTE"))])
async def ask(request: AskRequest, http_request: Request, db=Depends(get_db)):
    """
    Generate a grounded answer with structured citation blocks.
    Answer is sourced exclusively from retrieved knowledge base chunks.
    """
    return await answer(
        db=db,
        query=request.query,
        acl_groups=resolve_acl_groups(http_request, request.acl_groups),
        top_k=request.top_k,
    )
