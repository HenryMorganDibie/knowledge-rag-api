"""Feedback API — thumbs up/down and failure category capture."""

from typing import List, Literal, Optional
from datetime import datetime
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from core.database import get_db
from core.models import FeedbackLog
from core.security import rate_limit, require_service_key

router = APIRouter()


class FeedbackRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    answer: Optional[str] = Field(default=None, max_length=8000)
    rating: Literal["positive", "negative"]
    failure_category: Optional[Literal[
        "wrong_answer", "missing_citation", "stale_content", "irrelevant_chunks", "other"
    ]] = None
    comment: Optional[str] = Field(default=None, max_length=2000)
    chunk_ids: Optional[List[str]] = Field(default=None, max_length=50)
    user_id: Optional[str] = Field(default=None, max_length=256)


@router.post("/", dependencies=[Depends(require_service_key),
                                Depends(rate_limit("query", "RATE_LIMIT_QUERY_PER_MINUTE"))])
async def submit_feedback(request: FeedbackRequest, db=Depends(get_db)):
    """
    Record thumbs up/down feedback with optional failure category.
    Requires X-API-Key: feedback persists query text, so it is accepted only
    from the trusted application backend, not from anonymous browsers.
    """
    log = FeedbackLog(
        query=request.query,
        answer=request.answer,
        rating=request.rating,
        failure_category=request.failure_category,
        comment=request.comment,
        chunk_ids=request.chunk_ids or [],
        user_id=request.user_id,
        created_at=datetime.utcnow(),
    )
    db.add(log)
    await db.commit()
    return {"status": "recorded", "feedback_id": str(log.id)}
