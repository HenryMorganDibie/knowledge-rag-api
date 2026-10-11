"""Health check endpoints."""
from datetime import datetime

from fastapi import APIRouter, Depends, Request

from core.config import settings
from core.integrations import collect_status
from core.security import is_service_caller, rate_limit

router = APIRouter()


@router.get("/")
async def health():
    """Liveness: the process is up. Does not check dependencies."""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@router.get("/ready", dependencies=[Depends(rate_limit("status", "RATE_LIMIT_STATUS_PER_MINUTE"))])
async def readiness():
    """Readiness with the capabilities actually available on this deployment."""
    status = await collect_status()
    return {
        "status": "ready" if (status["live_query_ready"] or status["demo_mode"]) else "degraded",
        "live_query_ready": status["live_query_ready"],
        "demo_mode": status["demo_mode"],
        "checked_at": status["checked_at"],
    }


@router.get("/integrations", dependencies=[Depends(rate_limit("status", "RATE_LIMIT_STATUS_PER_MINUTE"))])
async def integrations(request: Request, refresh: bool = False):
    """
    Live status of every external integration, from real read-only checks.
    Results are cached; `refresh=true` bypasses the cache for service callers only.
    Never returns secret values.
    """
    status = await collect_status(force=refresh and is_service_caller(request))
    return {
        **status,
        "access": {
            "anonymous_live_queries": settings.ALLOW_ANONYMOUS_LIVE_QUERIES,
            "protected_operations_enabled": bool(settings.SERVICE_API_KEY),
        },
    }
