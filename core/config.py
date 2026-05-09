"""
Central configuration — all settings loaded from environment variables.
Swap DATABASE_URL from local PostgreSQL to Aurora by changing the env var.
"""

from typing import List
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    APP_ENV: str = "development"
    SECRET_KEY: str = "change-me-in-production"
    ALLOWED_ORIGINS: List[str] = ["*"]

    # Database (local pgvector or Aurora PostgreSQL)
    DATABASE_URL: str = "postgresql+asyncpg://rag:rag@localhost:5432/knowledge_rag"
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20

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

    # Reranker
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

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
