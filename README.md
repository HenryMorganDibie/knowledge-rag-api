# Knowledge RAG API

Production-grade Retrieval-Augmented Generation backend for internal technical knowledge bases.

Ingests content from **Document360** and **SharePoint**, processes text, tables, and images, stores embeddings in **Aurora PostgreSQL with pgvector**, and exposes a clean API for retrieval, grounded answer generation, feedback, and diagnostics.

---

## Architecture

```
                    ┌─────────────────────────────────────────────┐
                    │              Ingestion Layer                 │
                    │                                              │
    Document360 ───▶│  Connector → Fingerprint Check → Chunker   │
    SharePoint  ───▶│  → Image Describer → Embedder → S3 Upload  │
                    │  → Atomic Publish to Aurora PostgreSQL       │
                    └────────────────────┬────────────────────────┘
                                         │
                    ┌────────────────────▼────────────────────────┐
                    │           Aurora PostgreSQL + pgvector       │
                    │                                              │
                    │  document_sources  (canonical registry)      │
                    │  document_revisions (immutable audit trail)  │
                    │  document_chunks   (embeddings + BM25 GIN)   │
                    │  feedback_logs     (thumbs up/down)          │
                    │  ingestion_jobs    (run audit)               │
                    └────────────────────┬────────────────────────┘
                                         │
                    ┌────────────────────▼────────────────────────┐
                    │              Retrieval Layer                 │
                    │                                              │
                    │  Vector Search (HNSW cosine) +              │
                    │  BM25 Full-Text (tsvector/tsquery) +        │
                    │  RRF Merge + ACL Filter + Cross-Encoder     │
                    └────────────────────┬────────────────────────┘
                                         │
                    ┌────────────────────▼────────────────────────┐
                    │                  APIs (FastAPI)              │
                    │                                              │
                    │  POST /retrieve   Hybrid search + ACL       │
                    │  POST /ask        Grounded answer + citations│
                    │  POST /feedback   Thumbs up/down capture    │
                    │  POST /debug/trace Full retrieval trace      │
                    │  POST /ingest/*   Trigger ingestion sync     │
                    └─────────────────────────────────────────────┘
```

---

## Key Design Decisions

### Fingerprint-Based Change Detection
Every document is SHA-256 fingerprinted on raw content before any processing begins. Unchanged documents are skipped entirely — no re-chunking, no re-embedding, no S3 writes. This keeps incremental syncs fast even at scale.

### Atomic Chunk Publishing
Old chunks are deleted and new chunks inserted in a single database transaction. There is no window where a query can return a mix of stale and fresh chunks for the same document. This is the most critical correctness guarantee in the system.

### Structure-Aware Chunking
The chunker walks the HTML DOM rather than splitting on raw character count. Every chunk carries its full `section_path` (e.g. `"Setup > Installation > Windows"`) and `heading` so retrieval context is never lost. Tables are serialized to markdown. Images are described by GPT-4o vision so diagrams and screenshots are searchable.

### Hybrid Retrieval with RRF
Vector search and BM25 full-text search run in parallel. Results are merged using Reciprocal Rank Fusion — chunks appearing in both ranked lists get a significant boost. A cross-encoder reranker (sentence-transformers) handles final precision ordering.

### ACL Filtering
Every chunk stores the ACL groups from its source document. The retrieval layer filters chunks at query time — a user only sees chunks their group has access to. ACL bleed (returning restricted chunks to unauthorized users) is tested explicitly.

### Presigned S3 Citation URLs
Source documents are stored in S3. Citation endpoints return time-limited presigned URLs — callers get temporary, auth-gated access to the original document without any credentials being exposed.

---

## Project Structure

```
knowledge-rag-api/
├── api/
│   ├── main.py                  # FastAPI app + lifespan
│   └── routes/
│       ├── health.py
│       ├── ingest.py            # Ingestion triggers
│       ├── retrieval.py         # Hybrid search endpoint
│       ├── orchestrator.py      # Grounded answer endpoint
│       ├── feedback.py          # Thumbs up/down capture
│       └── debug.py             # Retrieval trace endpoint
├── core/
│   ├── config.py                # All settings via env vars
│   ├── database.py              # Async SQLAlchemy + pgvector init
│   ├── models.py                # ORM models
│   └── logger.py                # CloudWatch-friendly JSON logger
├── ingestion/
│   ├── pipeline.py              # Core ingestion with atomic publish
│   ├── connectors/
│   │   ├── document360.py       # Document360 REST API connector
│   │   └── sharepoint.py        # Microsoft Graph / SharePoint connector
│   └── processors/
│       ├── chunker.py           # Structure-aware HTML chunker
│       ├── embedder.py          # OpenAI batch embedding
│       └── image_describer.py   # GPT-4o vision image description
├── retrieval/
│   └── hybrid_retriever.py      # Vector + BM25 + RRF + reranking
├── orchestrator/
│   └── answer_engine.py         # Grounded LLM answer generation
├── storage/
│   └── s3_client.py             # S3/MinIO abstraction + presigned URLs
├── tests/
│   ├── unit/
│   │   ├── test_chunker.py
│   │   ├── test_fingerprint.py
│   │   └── test_retriever.py
│   └── integration/
│       └── test_pipeline.py
├── docker-compose.yml           # PostgreSQL + pgvector + MinIO
├── Dockerfile
├── requirements.txt
├── alembic.ini
└── .env.example
```

---

## Quickstart (Local Dev)

### 1. Clone and configure

```bash
git clone https://github.com/HenryMorganDibie/knowledge-rag-api.git
cd knowledge-rag-api
cp .env.example .env
# Fill in OPENAI_API_KEY and optionally Document360/SharePoint credentials
```

### 2. Start infrastructure

```bash
docker-compose up db minio -d
```

### 3. Install dependencies

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Run the API

```bash
uvicorn api.main:app --reload
```

The API will be live at `http://localhost:8000/docs`

On first startup, the app automatically:
- Enables the `pgvector` extension
- Creates all tables
- Builds HNSW and GIN indexes

### 5. Run tests

```bash
pytest tests/ -v
```

---

## API Reference

### `POST /retrieve`
Hybrid vector + BM25 search with ACL filtering and reranking.

```json
{
  "query": "How do I configure SSO?",
  "acl_groups": ["engineering", "it-ops"],
  "top_k": 5,
  "diagnostics": true
}
```

### `POST /ask`
Grounded answer generation with structured citation blocks.

```json
{
  "query": "What are the rate limits for the REST API?",
  "acl_groups": ["engineering"],
  "top_k": 5
}
```

Response:
```json
{
  "answer": "The REST API enforces a limit of 100 requests per minute per API key...",
  "citations": [
    {
      "chunk_id": "3f2a...",
      "section_path": "API Reference > Rate Limiting",
      "heading": "Rate Limiting",
      "excerpt": "The API enforces 100 requests per minute..."
    }
  ],
  "chunks_used": 3
}
```

### `POST /feedback`
Capture thumbs up/down with optional failure category.

```json
{
  "query": "How do I reset my password?",
  "rating": "negative",
  "failure_category": "wrong_answer",
  "comment": "Answer was about API keys, not user passwords",
  "chunk_ids": ["abc123", "def456"]
}
```

### `POST /debug/trace`
Full retrieval trace showing vector scores, BM25 ranks, RRF merge, and rerank scores.

### `POST /ingest/document360`
Trigger a full Document360 sync (runs in background, returns job ID).

### `POST /ingest/sharepoint`
Trigger a full SharePoint sync.

---

## Production Deployment (AWS)

| Component | AWS Service |
|-----------|-------------|
| API | ECS Fargate (containerized FastAPI) |
| Database | Aurora PostgreSQL + pgvector |
| Raw storage | S3 (raw docs + images) |
| Chunk artifacts | S3 (JSON chunk snapshots) |
| Async ingestion | SQS + EventBridge scheduled triggers |
| Secrets | AWS Secrets Manager |
| Observability | CloudWatch (structured JSON logs) |

To switch from local PostgreSQL to Aurora, update `DATABASE_URL` in your environment:
```
DATABASE_URL=postgresql+asyncpg://user:pass@your-aurora-cluster.rds.amazonaws.com:5432/knowledge_rag
```

To use real AWS S3 instead of MinIO, leave `S3_ENDPOINT_URL` empty and set proper IAM credentials.

---

## Retrieval Quality Evaluation

The system is evaluated across four dimensions:

| Metric | What it tests |
|--------|--------------|
| Chunk boundary coherence | Chunks don't split mid-sentence or mid-table |
| Citation grounding rate | Every claim in the answer maps to a retrieved chunk |
| Stale content prevention | Re-ingested documents never return old chunks |
| ACL safety | Restricted chunks never surface for unauthorized groups |

---

## Environment Variables

See `.env.example` for the full list. Key variables:

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string (asyncpg) |
| `OPENAI_API_KEY` | Used for embeddings and LLM answer generation |
| `S3_ENDPOINT_URL` | Leave empty for AWS S3; set for local MinIO |
| `DOCUMENT360_API_KEY` | Document360 API token |
| `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` | Microsoft Graph credentials for SharePoint |
| `EMBEDDING_MODEL` | Default: `text-embedding-3-small` |
| `LLM_MODEL` | Default: `gpt-4o` |
| `CHUNK_SIZE` | Token target per chunk (default: 512) |

---

## License

MIT
