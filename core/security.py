"""
Request-level security primitives for a publicly reachable deployment.

- Sliding-window rate limiting per client IP (in-process; put a shared
  limiter such as an API gateway or Redis in front for multi-instance deploys).
- Service API key verification (constant-time compare) for protected routes.
- Server-side ACL resolution: anonymous callers are always public-only,
  whatever `acl_groups` they put in the request body.
"""

import hmac
import math
import threading
import time
from collections import defaultdict, deque
from typing import Callable, Deque, Dict, List, Optional

from fastapi import HTTPException, Request

from core.config import settings

API_KEY_HEADER = "X-API-Key"


def client_ip(request: Request) -> str:
    if settings.TRUST_FORWARDED_FOR:
        forwarded = request.headers.get("x-forwarded-for", "")
        # The right-most entry is the one appended by our own proxy; entries
        # to its left are client-controlled and spoofable.
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        if hops:
            return hops[-1]
    return request.client.host if request.client else "unknown"


class RateLimiter:
    """Sliding-window limiter keyed by (bucket, client IP)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: float = 60.0) -> Optional[int]:
        """Record a hit. Returns None if allowed, else seconds until retry."""
        now = self._clock()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                return max(1, math.ceil(window_seconds - (now - hits[0])))
            hits.append(now)
            if len(self._hits) > 50_000:  # bound memory under IP churn
                self._hits = defaultdict(deque, {k: v for k, v in self._hits.items() if v})
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def rate_limit(bucket: str, limit_setting: str):
    """FastAPI dependency factory: `Depends(rate_limit("query", "RATE_LIMIT_QUERY_PER_MINUTE"))`."""

    async def _dependency(request: Request) -> None:
        limit = getattr(settings, limit_setting)
        retry_after = limiter.check(f"{bucket}:{client_ip(request)}", limit)
        if retry_after is not None:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded for {bucket} requests ({limit}/minute). Retry later.",
                headers={"Retry-After": str(retry_after)},
            )

    return _dependency


def is_service_caller(request: Request) -> bool:
    expected = settings.SERVICE_API_KEY
    supplied = request.headers.get(API_KEY_HEADER, "")
    if not expected or not supplied:
        return False
    return hmac.compare_digest(supplied.encode(), expected.encode())


async def require_service_key(request: Request) -> None:
    """Protect destructive, costly, or diagnostic operations."""
    if not settings.SERVICE_API_KEY:
        raise HTTPException(
            status_code=403,
            detail="This operation is disabled on this deployment (no SERVICE_API_KEY configured).",
        )
    if not is_service_caller(request):
        raise HTTPException(status_code=401, detail=f"Missing or invalid {API_KEY_HEADER} header.")


async def require_live_query_access(request: Request) -> None:
    """Live queries spend embedding/LLM budget: keyed callers only unless explicitly opened."""
    if settings.ALLOW_ANONYMOUS_LIVE_QUERIES or is_service_caller(request):
        return
    raise HTTPException(
        status_code=401,
        detail=(
            "Live queries require an X-API-Key header on this deployment. "
            "Use the /demo endpoints to query the public sample knowledge base."
        ),
    )


def resolve_acl_groups(request: Request, requested: Optional[List[str]]) -> List[str]:
    """
    Decide which ACL groups a query may use. Never trust anonymous input.

    Only an authenticated service caller (a trusted gateway or backend that
    has itself authenticated the end user) may assert groups. Everyone else
    is treated as anonymous and sees public chunks only.
    """
    if requested and is_service_caller(request):
        return list(requested)
    return []
