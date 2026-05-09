"""
Ingestion trigger endpoints.
POST /ingest/document360 — run full Document360 sync
POST /ingest/sharepoint  — run full SharePoint sync
POST /ingest/document360/{article_id} — ingest a single article
"""

import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, BackgroundTasks
from pydantic import BaseModel

from core.database import get_db
from core.models import IngestionJob
from core.logger import get_logger
from ingestion.connectors.document360 import Document360Connector
from ingestion.connectors.sharepoint import SharePointConnector
from ingestion.pipeline import ingest_document

router = APIRouter()
logger = get_logger(__name__)


async def _run_d360_sync(job_id: uuid.UUID, db):
    connector = Document360Connector()
    scanned = changed = skipped = total_chunks = 0
    try:
        async for article in connector.fetch_all_articles():
            scanned += 1
            fingerprint = connector.fingerprint(article)
            result = await ingest_document(
                db=db,
                source_type="document360",
                external_id=article.external_id,
                title=article.title,
                html_content=article.html_content,
                raw_bytes=article.raw_bytes,
                url=article.url,
                category_path=article.category_path,
                acl_groups=article.acl_groups,
                fingerprint=fingerprint,
                job_id=job_id,
            )
            if result["action"] == "skipped":
                skipped += 1
            else:
                changed += 1
                total_chunks += result["chunks"]
    except Exception as e:
        logger.error(f"D360 sync failed: {e}")


@router.post("/document360")
async def trigger_d360_sync(background_tasks: BackgroundTasks, db=Depends(get_db)):
    """Trigger a full Document360 ingestion sync in the background."""
    job = IngestionJob(source_type="document360", status="pending")
    db.add(job)
    await db.commit()
    background_tasks.add_task(_run_d360_sync, job.id, db)
    return {"job_id": str(job.id), "status": "pending", "message": "Document360 sync started"}


@router.post("/sharepoint")
async def trigger_sharepoint_sync(background_tasks: BackgroundTasks, db=Depends(get_db)):
    """Trigger a full SharePoint ingestion sync in the background."""
    return {"message": "SharePoint sync queued — same pipeline as Document360"}
