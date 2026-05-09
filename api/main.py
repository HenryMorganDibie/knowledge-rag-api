"""
Knowledge RAG API — Production-grade retrieval-augmented generation backend.
Exposes Retrieval, Orchestrator, Feedback, and Debug endpoints.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import retrieval, orchestrator, feedback, debug, ingest, health
from core.config import settings
from core.database import init_db
from core.logger import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Knowledge RAG API...")
    await init_db()
    yield
    logger.info("Shutting down Knowledge RAG API...")


app = FastAPI(
    title="Knowledge RAG API",
    description=(
        "Production RAG backend for internal technical knowledge bases. "
        "Ingests from Document360 and SharePoint, stores embeddings in "
        "Aurora PostgreSQL with pgvector, and serves hybrid retrieval with "
        "grounded answer generation."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/health", tags=["Health"])
app.include_router(ingest.router, prefix="/ingest", tags=["Ingestion"])
app.include_router(retrieval.router, prefix="/retrieve", tags=["Retrieval"])
app.include_router(orchestrator.router, prefix="/ask", tags=["Orchestrator"])
app.include_router(feedback.router, prefix="/feedback", tags=["Feedback"])
app.include_router(debug.router, prefix="/debug", tags=["Debug"])
