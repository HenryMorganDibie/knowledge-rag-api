"""
Knowledge RAG API — Production-grade retrieval-augmented generation backend.
Exposes Retrieval, Orchestrator, Feedback, Debug, and a public Demo API.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.middleware import HardeningMiddleware
from api.routes import retrieval, orchestrator, feedback, debug, ingest, health, demo
from core.config import settings
from core.database import database_configured, init_db
from core.errors import UpstreamUnavailable
from core.logger import get_logger
from core.security import API_KEY_HEADER

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Knowledge RAG API...")
    if database_configured():
        try:
            await init_db()
        except Exception as e:
            if settings.REQUIRE_DATABASE_ON_STARTUP:
                raise
            # Keep serving demo mode and health diagnostics; live routes report 503/disconnected.
            logger.error(f"Database initialisation failed ({type(e).__name__}); live endpoints unavailable.")
    else:
        logger.info("DATABASE_URL not set: live endpoints disabled, demo mode only.")
    if settings.DEMO_MODE_ENABLED:
        from demo.engine import get_demo_store
        get_demo_store()  # build the sample index once, before traffic
    yield
    logger.info("Shutting down Knowledge RAG API...")


app = FastAPI(
    title="Knowledge RAG API",
    description=(
        "Production RAG backend for internal technical knowledge bases. "
        "Ingests from Document360 and SharePoint, stores embeddings in "
        "Aurora PostgreSQL with pgvector, and serves hybrid retrieval with "
        "grounded answer generation.\n\n"
        "**Access model.** `/demo/*` is public and serves a bundled fictional sample knowledge base "
        "(every response has `\"mode\": \"demo\"`). Ingestion, feedback and debug endpoints require an "
        f"`{API_KEY_HEADER}` header. Live `/retrieve` and `/ask` require it unless the deployment enables "
        "anonymous live queries; anonymous callers are always restricted to public chunks."
    ),
    version="1.1.0",
    lifespan=lifespan,
)

# Middleware added last runs outermost: HardeningMiddleware wraps CORS and every route.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,  # API keys travel in headers, never cookies
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", API_KEY_HEADER],
    expose_headers=["X-Request-ID", "Retry-After"],
    max_age=600,
)
app.add_middleware(HardeningMiddleware)



@app.exception_handler(UpstreamUnavailable)
async def upstream_unavailable(request: Request, exc: UpstreamUnavailable):
    logger.warning(f"Upstream unavailable on {request.url.path}: {exc.service}: {exc.reason}")
    return JSONResponse(status_code=503, content={
        "detail": f"{exc.service} unavailable: {exc.reason}. Check /health/integrations.",
    })


app.include_router(health.router, prefix="/health", tags=["Health"])
app.include_router(demo.router, prefix="/demo", tags=["Demo (public sample data)"])
app.include_router(ingest.router, prefix="/ingest", tags=["Ingestion"])
app.include_router(retrieval.router, prefix="/retrieve", tags=["Retrieval"])
app.include_router(orchestrator.router, prefix="/ask", tags=["Orchestrator"])
app.include_router(feedback.router, prefix="/feedback", tags=["Feedback"])
app.include_router(debug.router, prefix="/debug", tags=["Debug"])
