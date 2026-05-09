"""Feedback API — thumbs up/down and failure category capture."""

from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.database import get_db
from core.models import FeedbackLog

router = APIRouter()


class FeedbackRequest(BaseModel):
    query: str
    answer: Optional[str] = None
    rating: str                        # "positive" | "negative"
    failure_category: Optional[str] = None
    comment: Optional[str] = None
    chunk_ids: Optional[List[str]] = None
    user_id: Optional[str] = None


@router.post("/")
async def submit_feedback(request: FeedbackRequest, db=Depends(get_db)):
    """Record thumbs up/down feedback with optional failure category."""
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
