"""
Integration health checks.

Every status is derived from an actual check, never from configuration
alone. States:
  connected     configured, and a live read-only check succeeded
  disconnected  configured, but the live check failed (reason is sanitised)
  unavailable   not configured on this deployment
  demo          served from the bundled sample dataset (no external service)

Checks are read-only and free of model/token cost, run concurrently with a
timeout, and are cached so anonymous traffic cannot hammer upstream APIs.
Secret values are never included in results or error messages.
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, List, Optional

import httpx

from core.config import settings
from core.logger import get_logger

logger = get_logger(__name__)

CONNECTED, DISCONNECTED, UNAVAILABLE, DEMO = "connected", "disconnected", "unavailable", "demo"

_cache: Dict[str, object] = {"at": 0.0, "value": None}
_lock = asyncio.Lock()


def _result(key: str, name: str, state: str, detail: str, *, missing: Optional[List[str]] = None,
            checked: Optional[str] = None, latency_ms: Optional[float] = None, facts: Optional[dict] = None) -> dict:
    return {
        "key": key,
        "name": name,
        "state": state,
        "detail": detail,
        "check": checked,
        "missing_config": missing or [],
        "latency_ms": latency_ms,
        "facts": facts or {},
    }


def _describe_error(exc: BaseException) -> str:
    """Map exceptions to a safe category. Raw messages can embed hosts, IDs or tokens."""
    if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException)):
        return "timed out"
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return f"authentication rejected (HTTP {code})"
        if code == 404:
            return "resource not found (HTTP 404); check the configured ID"
        return f"upstream returned HTTP {code}"
    if isinstance(exc, httpx.HTTPError):
        return "network error reaching the service"
    name = type(exc).__name__
    if "Auth" in name or "Password" in name or "Credential" in name:
        return "authentication failed"
    if name in ("ConnectionRefusedError", "OSError", "gaierror", "ConnectionError"):
        return "connection failed"
    return f"check failed ({name})"


async def _timed(fn: Callable[[], Awaitable[dict]]) -> tuple[Optional[dict], Optional[BaseException], float]:
    start = time.perf_counter()
    try:
        facts = await asyncio.wait_for(fn(), timeout=settings.INTEGRATION_CHECK_TIMEOUT_SECONDS)
        return facts, None, round((time.perf_counter() - start) * 1000, 1)
    except Exception as exc:  # noqa: BLE001 - every failure becomes a sanitised status
        return None, exc, round((time.perf_counter() - start) * 1000, 1)


def _missing(**pairs: str) -> List[str]:
    return [name for name, value in pairs.items() if not str(value).strip()]


async def check_database() -> List[dict]:
    """PostgreSQL connectivity, then pgvector availability and chunk counts."""
    from core.database import database_configured

    if not database_configured():
        msg = "DATABASE_URL is not set; live retrieval is disabled on this deployment."
        return [
            _result("postgres", "PostgreSQL", UNAVAILABLE, msg, missing=["DATABASE_URL"]),
            _result("pgvector", "pgvector", UNAVAILABLE, "Requires PostgreSQL.", missing=["DATABASE_URL"]),
        ]

    async def probe() -> dict:
        from sqlalchemy import text
        from core.database import get_engine

        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
            ext = (await conn.execute(text(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"))).scalar_one_or_none()
            chunks = docs = None
            has_table = (await conn.execute(text("SELECT to_regclass('public.document_chunks')"))).scalar_one_or_none()
            if has_table:
                chunks = (await conn.execute(text("SELECT count(*) FROM document_chunks"))).scalar_one()
                docs = (await conn.execute(text("SELECT count(*) FROM document_sources"))).scalar_one()
            return {"pgvector_version": ext, "chunks": chunks, "documents": docs}

    facts, err, ms = await _timed(probe)
    if err is not None:
        reason = _describe_error(err)
        return [
            _result("postgres", "PostgreSQL", DISCONNECTED, f"Configured but unreachable: {reason}.",
                    checked="SELECT 1", latency_ms=ms),
            _result("pgvector", "pgvector", DISCONNECTED, "Cannot verify while PostgreSQL is unreachable.",
                    checked="pg_extension lookup"),
        ]
    pg = _result("postgres", "PostgreSQL", CONNECTED, "Connection and query succeeded.",
                 checked="SELECT 1", latency_ms=ms,
                 facts={"documents": facts["documents"], "chunks": facts["chunks"]})
    if facts["pgvector_version"]:
        vec = _result("pgvector", "pgvector", CONNECTED, f"Extension installed (v{facts['pgvector_version']}).",
                      checked="pg_extension lookup", facts={"version": facts["pgvector_version"]})
    else:
        vec = _result("pgvector", "pgvector", DISCONNECTED, "PostgreSQL reachable but the vector extension is not installed.",
                      checked="pg_extension lookup")
    return [pg, vec]


async def check_embeddings() -> dict:
    """Retrieves the embedding model's metadata: authenticated but costs no tokens."""
    name = f"OpenAI embeddings ({settings.EMBEDDING_MODEL})"
    if not settings.OPENAI_API_KEY.strip():
        return _result("embeddings", name, UNAVAILABLE, "OPENAI_API_KEY is not set.", missing=["OPENAI_API_KEY"])

    async def probe() -> dict:
        async with httpx.AsyncClient(timeout=settings.INTEGRATION_CHECK_TIMEOUT_SECONDS) as client:
            resp = await client.get(
                f"https://api.openai.com/v1/models/{settings.EMBEDDING_MODEL}",
                headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
            )
            resp.raise_for_status()
        return {"model": settings.EMBEDDING_MODEL, "dimensions": settings.EMBEDDING_DIM}

    facts, err, ms = await _timed(probe)
    check = f"GET /v1/models/{settings.EMBEDDING_MODEL}"
    if err is not None:
        return _result("embeddings", name, DISCONNECTED, f"Key configured but check failed: {_describe_error(err)}.",
                       checked=check, latency_ms=ms)
    return _result("embeddings", name, CONNECTED, "API key accepted; model available.", checked=check,
                   latency_ms=ms, facts=facts)


async def check_object_storage() -> dict:
    name = "Object storage (S3 / MinIO)"
    has_static_keys = bool(settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY)
    if not has_static_keys and not settings.S3_ENDPOINT_URL:
        return _result("object_storage", name, UNAVAILABLE,
                       "No S3 credentials or endpoint configured.",
                       missing=["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "S3_BUCKET_RAW"])

    def head_buckets() -> dict:
        import boto3
        from botocore.config import Config

        kwargs = {
            "region_name": settings.AWS_REGION,
            "aws_access_key_id": settings.AWS_ACCESS_KEY_ID or None,
            "aws_secret_access_key": settings.AWS_SECRET_ACCESS_KEY or None,
            "config": Config(connect_timeout=3, read_timeout=3, retries={"max_attempts": 1}),
        }
        if settings.S3_ENDPOINT_URL:
            kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL
        client = boto3.client("s3", **kwargs)
        for bucket in (settings.S3_BUCKET_RAW, settings.S3_BUCKET_ARTIFACTS):
            client.head_bucket(Bucket=bucket)
        return {"buckets": [settings.S3_BUCKET_RAW, settings.S3_BUCKET_ARTIFACTS]}

    async def probe() -> dict:
        return await asyncio.to_thread(head_buckets)

    facts, err, ms = await _timed(probe)
    if err is not None:
        code = getattr(err, "response", {}).get("Error", {}).get("Code") if hasattr(err, "response") else None
        reason = f"S3 error {code}" if code else _describe_error(err)
        return _result("object_storage", name, DISCONNECTED, f"Configured but check failed: {reason}.",
                       checked="HeadBucket", latency_ms=ms)
    return _result("object_storage", name, CONNECTED, "Both buckets reachable.", checked="HeadBucket",
                   latency_ms=ms, facts=facts)


async def check_document360() -> dict:
    """Issues the connector's own first list request with pageSize=1."""
    name = "Document360"
    missing = _missing(DOCUMENT360_API_KEY=settings.DOCUMENT360_API_KEY,
                       DOCUMENT360_PROJECT_ID=settings.DOCUMENT360_PROJECT_ID)
    if missing:
        return _result("document360", name, UNAVAILABLE, "Connector credentials not configured.", missing=missing)

    async def probe() -> dict:
        async with httpx.AsyncClient(timeout=settings.INTEGRATION_CHECK_TIMEOUT_SECONDS) as client:
            resp = await client.get(
                f"{settings.DOCUMENT360_BASE_URL}/articles",
                headers={"api_token": settings.DOCUMENT360_API_KEY},
                params={"projectVersionId": settings.DOCUMENT360_PROJECT_ID, "page": 1, "pageSize": 1},
            )
            resp.raise_for_status()
        return {}

    facts, err, ms = await _timed(probe)
    check = "GET {base}/articles?pageSize=1 (api_token header)"
    if err is not None:
        return _result("document360", name, DISCONNECTED, f"Configured but check failed: {_describe_error(err)}.",
                       checked=check, latency_ms=ms)
    return _result("document360", name, CONNECTED, "API token accepted; article listing reachable.",
                   checked=check, latency_ms=ms, facts=facts)


async def check_microsoft_graph() -> dict:
    """OAuth2 client-credentials token, then a read of the configured site."""
    name = "Microsoft Graph / SharePoint"
    missing = _missing(AZURE_TENANT_ID=settings.AZURE_TENANT_ID, AZURE_CLIENT_ID=settings.AZURE_CLIENT_ID,
                       AZURE_CLIENT_SECRET=settings.AZURE_CLIENT_SECRET,
                       SHAREPOINT_SITE_ID=settings.SHAREPOINT_SITE_ID)
    if missing:
        return _result("microsoft_graph", name, UNAVAILABLE, "App registration not configured.", missing=missing)

    async def probe() -> dict:
        from ingestion.connectors.sharepoint import GRAPH_BASE, token_url

        async with httpx.AsyncClient(timeout=settings.INTEGRATION_CHECK_TIMEOUT_SECONDS) as client:
            tok = await client.post(token_url(), data={
                "grant_type": "client_credentials",
                "client_id": settings.AZURE_CLIENT_ID,
                "client_secret": settings.AZURE_CLIENT_SECRET,
                "scope": "https://graph.microsoft.com/.default",
            })
            tok.raise_for_status()
            site = await client.get(
                f"{GRAPH_BASE}/sites/{settings.SHAREPOINT_SITE_ID}",
                params={"$select": "id,displayName"},
                headers={"Authorization": f"Bearer {tok.json()['access_token']}"},
            )
            site.raise_for_status()
            return {"site_name": site.json().get("displayName")}

    facts, err, ms = await _timed(probe)
    check = "client_credentials token + GET /sites/{site-id}"
    if err is not None:
        return _result("microsoft_graph", name, DISCONNECTED, f"Configured but check failed: {_describe_error(err)}.",
                       checked=check, latency_ms=ms)
    return _result("microsoft_graph", name, CONNECTED, "Token issued and site readable.", checked=check,
                   latency_ms=ms, facts=facts)


def check_demo() -> dict:
    if not settings.DEMO_MODE_ENABLED:
        return _result("demo_kb", "Sample knowledge base", UNAVAILABLE, "Demo mode disabled (DEMO_MODE_ENABLED=false).")
    from demo.engine import get_demo_store

    store = get_demo_store()
    return _result(
        "demo_kb", "Sample knowledge base", DEMO,
        "Bundled fictional documents indexed in-process at startup with the production chunker.",
        checked="in-process index", facts={
            "collections": len(store.collections),
            "documents": sum(len(c.documents) for c in store.collections.values()),
            "chunks": sum(len(c.chunks) for c in store.collections.values()),
        },
    )


async def collect_status(force: bool = False) -> dict:
    async with _lock:
        age = time.monotonic() - float(_cache["at"])
        if not force and _cache["value"] is not None and age < settings.INTEGRATION_STATUS_CACHE_SECONDS:
            return {**_cache["value"], "cached": True, "cache_age_seconds": round(age, 1)}

        db, emb, s3, d360, graph = await asyncio.gather(
            check_database(), check_embeddings(), check_object_storage(),
            check_document360(), check_microsoft_graph(),
        )
        integrations = [d360, graph, *db, emb, s3, check_demo()]
        live_ready = all(i["state"] == CONNECTED for i in integrations if i["key"] in ("postgres", "pgvector", "embeddings"))
        value = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "live_query_ready": live_ready,
            "demo_mode": settings.DEMO_MODE_ENABLED,
            "integrations": integrations,
        }
        _cache.update(at=time.monotonic(), value=value)
        return {**value, "cached": False, "cache_age_seconds": 0.0}


def clear_cache() -> None:
    _cache.update(at=0.0, value=None)
