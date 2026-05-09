"""Orchestrator API — grounded answer generation with citation blocks."""

from typing import List, Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.database import get_db
from orchestrator.answer_engine import answer

router = APIRouter()


class AskRequest(BaseModel):
    query: str
    acl_groups: Optional[List[str]] = None
    top_k: int = 5


@router.post("/")
async def ask(request: AskRequest, db=Depends(get_db)):
    """
    Generate a grounded answer with structured citation blocks.
    Answer is sourced exclusively from retrieved knowledge base chunks.
    """
    return await answer(
        db=db,
        query=request.query,
        acl_groups=request.acl_groups,
        top_k=request.top_k,
    )
