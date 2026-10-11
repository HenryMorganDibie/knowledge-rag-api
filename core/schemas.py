"""Shared request validation for the live query endpoints."""

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class LiveQueryBase(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    acl_groups: Optional[List[str]] = Field(
        default=None,
        max_length=20,
        description="Honoured only for authenticated service callers (X-API-Key). "
                    "Anonymous callers are always restricted to public chunks.",
    )
    top_k: int = Field(default=5, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def _strip_query(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query must not be blank")
        return v

    @field_validator("acl_groups")
    @classmethod
    def _check_groups(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None and any(not g.strip() or len(g) > 128 for g in v):
            raise ValueError("acl_groups entries must be non-empty and at most 128 characters")
        return v
