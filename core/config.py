"""
Central configuration — all settings loaded from environment variables.
Swap DATABASE_URL from local PostgreSQL to Aurora by changing the env var.
"""

import json
from typing import List

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    APP_ENV: str = "development"
    SECRET_KEY: str = "change-me-in-production"
    # Comma-separated or JSON list of browser origins allowed by CORS.
    # Never "*" in production: the demo frontend origin(s) only.
    ALLOWED_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Database (local pgvector or Aurora PostgreSQL). Empty = not configured:
    # live endpoints return 503 and the demo mode remains available.
    DATABASE_URL: str = ""
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20
    # Fail startup if the database cannot be initialised (use for live deployments).
    REQUIRE_DATABASE_ON_STARTUP: bool = False

    # S3 / MinIO storage
    S3_ENDPOINT_URL: str = ""           # empty = real AWS S3; set for MinIO
    S3_BUCKET_RAW: str = "rag-raw-docs"
    S3_BUCKET_ARTIFACTS: str = "rag-chunk-artifacts"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"

    # Embeddings
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIM: int = 1536
    OPENAI_API_KEY: str = ""

    # Reranker (cross-encoder needs torch; disable on lightweight hosts)
    RERANKER_ENABLED: bool = True
    RERANKER_MODEL: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    RERANKER_TOP_K: int = 5

    # Retrieval defaults
    RETRIEVAL_VECTOR_TOP_K: int = 20
    RETRIEVAL_BM25_TOP_K: int = 20
    RETRIEVAL_FINAL_TOP_K: int = 5

    # Document360
    DOCUMENT360_API_KEY: str = ""
    DOCUMENT360_BASE_URL: str = "https://apihub.document360.io/v2"
    DOCUMENT360_PROJECT_ID: str = ""

    # SharePoint / Microsoft Graph
    AZURE_TENANT_ID: str = ""
    AZURE_CLIENT_ID: str = ""
    AZURE_CLIENT_SECRET: str = ""
    SHAREPOINT_SITE_ID: str = ""

    # LLM (orchestrator)
    LLM_MODEL: str = "gpt-4o"
    LLM_MAX_TOKENS: int = 1024
    LLM_TEMPERATURE: float = 0.0

    # Chunking
    CHUNK_SIZE: int = 512
    CHUNK_OVERLAP: int = 64

    # Presigned URL TTL (seconds)
    PRESIGNED_URL_TTL: int = 3600

    # --- Public deployment controls ---
    # Server-side key for protected operations (ingestion, feedback, debug,
    # live queries). Sent by trusted callers as the X-API-Key header.
    SERVICE_API_KEY: str = ""
    # When false (default), live /retrieve and /ask require SERVICE_API_KEY so
    # anonymous visitors cannot trigger paid embedding/LLM calls.
    ALLOW_ANONYMOUS_LIVE_QUERIES: bool = False
    # Bundled public sample knowledge base served under /demo.
    DEMO_MODE_ENABLED: bool = True

    # Abuse limits (per client IP, per process)
    RATE_LIMIT_QUERY_PER_MINUTE: int = 30
    RATE_LIMIT_INGEST_PER_MINUTE: int = 10
    RATE_LIMIT_STATUS_PER_MINUTE: int = 30
    MAX_REQUEST_BYTES: int = 32_768
    REQUEST_TIMEOUT_SECONDS: float = 30.0
    # Only enable behind a proxy that overwrites X-Forwarded-For.
    TRUST_FORWARDED_FOR: bool = False

    # Integration health checks
    INTEGRATION_CHECK_TIMEOUT_SECONDS: float = 5.0
    INTEGRATION_STATUS_CACHE_SECONDS: int = 60

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"

    @property
    def allowed_origins(self) -> List[str]:
        raw = self.ALLOWED_ORIGINS.strip()
        if raw.startswith("["):
            return [str(o).strip() for o in json.loads(raw) if str(o).strip()]
        return [o.strip() for o in raw.split(",") if o.strip()]


settings = Settings()
