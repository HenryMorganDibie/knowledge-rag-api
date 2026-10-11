"""
Ingestion trigger endpoints (all require X-API-Key).
POST /ingest/document360 — run full Document360 sync
POST /ingest/sharepoint  — run full SharePoint sync
GET  /ingest/jobs        — recent ingestion jobs
GET  /ingest/jobs/{id}   — one job's status and counters

Sync targets come only from server-side configuration; callers cannot
supply URLs, hosts or site IDs, so these endpoints cannot be used for SSRF.
"""

import html
import uuid
from datetime import datetime
from typing import AsyncIterator, Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select

from core.config import settings
from core.database import AsyncSessionLocal, get_db
from core.models import IngestionJob
from core.logger import get_logger
from core.security import rate_limit, require_service_key
from ingestion.connectors.document360 import Document360Connector
from ingestion.connectors.sharepoint import SharePointConnector
from ingestion.pipeline import ingest_document

router = APIRouter(dependencies=[Depends(require_service_key)])
logger = get_logger(__name__)


def _text_to_html(text: str) -> str:
    """SharePoint files arrive as extracted text; wrap paragraphs so the HTML chunker can process them."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return "\n".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)


async def _document360_items(connector: Document360Connector) -> AsyncIterator[dict]:
    async for article in connector.fetch_all_articles():
        yield dict(
            external_id=article.external_id, title=article.title, html_content=article.html_content,
            raw_bytes=article.raw_bytes, url=article.url, category_path=article.category_path,
            acl_groups=article.acl_groups, fingerprint=connector.fingerprint(article),
        )


async def _sharepoint_items(connector: SharePointConnector) -> AsyncIterator[dict]:
    async for f in connector.fetch_all_files():
        yield dict(
            external_id=f.external_id, title=f.title,
            html_content=_text_to_html(f.content),
            raw_bytes=f.raw_bytes, url=f.url, category_path=f.title,
            acl_groups=f.acl_groups, fingerprint=connector.fingerprint(f),
        )


async def run_sync(job_id: uuid.UUID, source_type: str, items: Callable[[], AsyncIterator[dict]]) -> None:
    """
    Background sync with its own session (the request's session is closed by
    the time background tasks run). Counters and failures are persisted on the job.
    """
    scanned = changed = skipped = total_chunks = 0
    async with AsyncSessionLocal() as db:
        job = await db.get(IngestionJob, job_id)
        job.status = "running"
        await db.commit()
        try:
            async for item in items():
                scanned += 1
                result = await ingest_document(db=db, source_type=source_type, job_id=job_id, **item)
                if result["action"] == "skipped":
                    skipped += 1
                else:
                    changed += 1
                    total_chunks += result["chunks"]
            job.status = "completed"
        except Exception as e:  # noqa: BLE001 - recorded on the job, never returned raw to clients
            await db.rollback()
            job = await db.get(IngestionJob, job_id)
            job.status = "failed"
            job.error_detail = f"{type(e).__name__} after {scanned} documents; see server logs."
            logger.exception(f"{source_type} sync failed job_id={job_id}")
        job.documents_scanned = scanned
        job.documents_changed = changed
        job.documents_skipped = skipped
        job.chunks_created = total_chunks
        job.completed_at = datetime.utcnow()
        await db.commit()


def _require_configured(source: str, **values: str) -> None:
    missing = [k for k, v in values.items() if not v]
    if missing:
        raise HTTPException(status_code=503, detail=f"{source} connector not configured: missing {', '.join(missing)}.")


async def _start(source_type: str, items, background_tasks: BackgroundTasks, db) -> dict:
    job = IngestionJob(source_type=source_type, status="pending")
    db.add(job)
    await db.commit()
    background_tasks.add_task(run_sync, job.id, source_type, items)
    return {"job_id": str(job.id), "status": "pending", "message": f"{source_type} sync started"}


@router.post("/document360", dependencies=[Depends(rate_limit("ingest", "RATE_LIMIT_INGEST_PER_MINUTE"))])
async def trigger_d360_sync(background_tasks: BackgroundTasks, db=Depends(get_db)):
    """Trigger a full Document360 ingestion sync in the background."""
    _require_configured("Document360", DOCUMENT360_API_KEY=settings.DOCUMENT360_API_KEY,
                        DOCUMENT360_PROJECT_ID=settings.DOCUMENT360_PROJECT_ID)
    connector = Document360Connector()
    return await _start("document360", lambda: _document360_items(connector), background_tasks, db)


@router.post("/sharepoint", dependencies=[Depends(rate_limit("ingest", "RATE_LIMIT_INGEST_PER_MINUTE"))])
async def trigger_sharepoint_sync(background_tasks: BackgroundTasks, db=Depends(get_db)):
    """Trigger a full SharePoint (Microsoft Graph) ingestion sync in the background."""
    _require_configured("SharePoint", AZURE_TENANT_ID=settings.AZURE_TENANT_ID,
                        AZURE_CLIENT_ID=settings.AZURE_CLIENT_ID, AZURE_CLIENT_SECRET=settings.AZURE_CLIENT_SECRET,
                        SHAREPOINT_SITE_ID=settings.SHAREPOINT_SITE_ID)
    connector = SharePointConnector()
    return await _start("sharepoint", lambda: _sharepoint_items(connector), background_tasks, db)


def _job_dict(job: IngestionJob) -> dict:
    return {
        "job_id": str(job.id),
        "source_type": job.source_type,
        "status": job.status,
        "documents_scanned": job.documents_scanned,
        "documents_changed": job.documents_changed,
        "documents_skipped": job.documents_skipped,
        "chunks_created": job.chunks_created,
        "error_detail": job.error_detail,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


@router.get("/jobs")
async def list_jobs(limit: int = 20, db=Depends(get_db)):
    """Most recent ingestion jobs."""
    limit = max(1, min(limit, 100))
    rows = (await db.execute(select(IngestionJob).order_by(IngestionJob.started_at.desc()).limit(limit))).scalars()
    return {"jobs": [_job_dict(j) for j in rows]}


@router.get("/jobs/{job_id}")
async def get_job(job_id: uuid.UUID, db=Depends(get_db)):
    """Status and counters for one ingestion job."""
    job = await db.get(IngestionJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return _job_dict(job)
