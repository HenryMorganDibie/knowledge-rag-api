# Knowledge RAG API

Production-grade Retrieval-Augmented Generation backend for internal technical knowledge bases.

Ingests content from **Document360** and **SharePoint**, processes text, tables, and images, stores embeddings in **Aurora PostgreSQL with pgvector**, and exposes a clean API for retrieval, grounded answer generation, feedback, and diagnostics.

---

## Live Demo

| | URL |
|---|---|
| Interactive demo (frontend) | https://knowledge-rag-api-demo.vercel.app |
| API (FastAPI) | https://knowledge-rag-api.vercel.app |
| Swagger UI / ReDoc | https://knowledge-rag-api.vercel.app/docs · https://knowledge-rag-api.vercel.app/redoc |
| Integration status (live checks) | https://knowledge-rag-api.vercel.app/health/integrations |

The public deployment runs in **demo mode**: no database, embedding key or connector credentials are configured, and the status
endpoint reports each of those integrations as `unavailable` rather than pretending they are connected. Visitors query a bundled,
fictional sample knowledge base through `/demo/*`, which runs the production chunker, SHA-256 fingerprinting and Reciprocal Rank
Fusion code with deterministic stand-ins for embeddings, BM25 and the LLM (see [Demo mode](#demo-mode)).

![Knowledge RAG API Swagger UI](docs/images/swagger-ui.jpg)

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
                    │  Full-Text (tsvector/ts_rank, BM25-style) + │
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
Every chunk stores the ACL groups from its source document. The retrieval layer filters chunks at query time and **fails closed**: a caller who supplies no groups sees public chunks only, and a caller with groups sees public chunks plus chunks sharing a group. ACL bleed is covered by unit tests (`tests/unit/test_acl.py`) and by a PostgreSQL-backed test (`tests/integration/test_acl_postgres.py`, runs when `TEST_DATABASE_URL` is set).

> **Trust boundary:** `acl_groups` in a request body is honoured **only** for callers presenting the service key (`X-API-Key`), i.e. a trusted gateway that has itself authenticated the end user. Anonymous callers are always restricted to public chunks, whatever groups they send. For multi-user production use, derive groups from verified JWT/SSO claims at that gateway; this API does not implement end-user authentication itself.

### Raw Document Storage
Raw source files and chunk artifacts are written to S3 (or MinIO) before the database commit, keyed by source, document and revision. `storage/s3_client.py` provides `generate_presigned_url` for time-limited access; `/ask` citations currently return chunk IDs, section paths and excerpts, not presigned URLs.

---

## Project Structure

```
knowledge-rag-api/
├── api/
│   ├── main.py                  # FastAPI app + lifespan
│   ├── middleware.py            # Size/timeout limits, security headers, opaque errors
│   └── routes/
│       ├── health.py            # Liveness, readiness, live integration checks
│       ├── demo.py              # Public demo endpoints (sample data)
│       ├── ingest.py            # Ingestion triggers
│       ├── retrieval.py         # Hybrid search endpoint
│       ├── orchestrator.py      # Grounded answer endpoint
│       ├── feedback.py          # Thumbs up/down capture
│       └── debug.py             # Retrieval trace endpoint
├── core/
│   ├── config.py                # All settings via env vars
│   ├── database.py              # Async SQLAlchemy + pgvector init
│   ├── models.py                # ORM models
│   ├── integrations.py          # Real read-only health checks per integration
│   ├── security.py              # API key, rate limiting, server-side ACL resolution
│   └── logger.py                # CloudWatch-friendly JSON logger
├── demo/
│   ├── engine.py                # Demo index, retrieval, extractive answers, sandboxed sync
│   └── corpus/                  # Fictional sample documents + manifest
├── web/                         # Vite + React + TypeScript demo frontend
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
├── docs/
│   └── images/
│       └── swagger-ui.jpg       # Live API screenshot
├── docker-compose.yml           # PostgreSQL + pgvector + MinIO
├── Dockerfile
├── requirements.txt
├── alembic.ini
└── .env.example
```

---

## Quickstart

### Option A — GitHub Codespaces (Recommended)

The fastest way to run the full stack with zero local setup.

1. Click the green **Code** button on this repo → **Codespaces** tab → **Create codespace on main**
2. Wait ~60 seconds for the environment to load, then in the terminal:

```bash
cp .env.example .env
# Open .env and add your OPENAI_API_KEY
```

3. Start the database and MinIO storage:

```bash
docker-compose up db minio -d
```

4. Install dependencies:

```bash
pip install -r requirements.txt
```

5. Run the API:

```bash
python -m uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

6. Go to the **Ports** tab in VS Code → click the 🌐 globe icon next to port **8000** → add `/docs` to the URL.

> **Tip:** Store your `OPENAI_API_KEY` under repo **Settings → Secrets → Codespaces** so it's injected automatically every time you open the Codespace.

---

### Option B — Local Dev

#### 1. Clone and configure

```bash
git clone https://github.com/HenryMorganDibie/knowledge-rag-api.git
cd knowledge-rag-api
cp .env.example .env
# Fill in OPENAI_API_KEY and optionally Document360/SharePoint credentials
```

#### 2. Start infrastructure

```bash
docker-compose up db minio -d
```

#### 3. Install dependencies

```bash
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-rerank.txt   # optional cross-encoder (torch); otherwise set RERANKER_ENABLED=false
```

To run **demo mode only** (no database or keys), skip step 2 and start the API with `RERANKER_ENABLED=false`.

#### 4. Run the API

```bash
python -m uvicorn api.main:app --reload
```

The API will be live at `http://localhost:8000/docs`

On first startup, the app automatically:
- Enables the `pgvector` extension
- Creates all tables
- Builds HNSW and GIN indexes

#### 5. Run tests

```bash
pytest tests/ -v
# Include the PostgreSQL + pgvector ACL test:
TEST_DATABASE_URL=postgresql+asyncpg://rag:rag@localhost:5432/knowledge_rag pytest tests/ -v
```

#### 6. Run the demo frontend

```bash
cd web
npm install
VITE_API_BASE_URL=http://localhost:8000 npm run dev   # http://localhost:5173
npm run lint && npm run typecheck && npm run build    # checks used before deploying
```

---

## API Reference

Full, always-current contracts are served at `/docs` (Swagger UI), `/redoc` and `/openapi.json`.

| Endpoint | Access | Purpose |
|---|---|---|
| `GET /health/` | public | Liveness |
| `GET /health/ready` | public | Readiness: whether live queries and/or demo mode are available |
| `GET /health/integrations` | public, cached | Live read-only checks of every integration (never returns secrets) |
| `GET /demo/collections` | public | Sample collections, public documents, fingerprints, chunk counts |
| `GET /demo/documents/{id}` | public | A sample document's chunks (restricted documents return 404) |
| `POST /demo/retrieve` | public, rate limited | Hybrid / vector / keyword retrieval with a scoring trace |
| `POST /demo/ask` | public, rate limited | Extractive grounded answer with numbered citations |
| `POST /demo/ingest` | public, rate limited | Sandboxed sync over an allow-listed sample source |
| `POST /retrieve/` | `X-API-Key`* | Live hybrid retrieval (pgvector + full-text + RRF + rerank) |
| `POST /ask/` | `X-API-Key`* | Live grounded answer via the configured LLM |
| `POST /debug/trace` | `X-API-Key` | Full live retrieval trace |
| `POST /feedback/` | `X-API-Key` | Store thumbs up/down with a failure category |
| `POST /ingest/document360` · `POST /ingest/sharepoint` | `X-API-Key` | Start a background sync; returns a job ID |
| `GET /ingest/jobs` · `GET /ingest/jobs/{id}` | `X-API-Key` | Job status, counters and sanitised errors |

\* Public only if `ALLOW_ANONYMOUS_LIVE_QUERIES=true`; anonymous callers are then restricted to public chunks.

```bash
curl -X POST https://knowledge-rag-api.vercel.app/demo/ask \
  -H 'Content-Type: application/json' \
  -d '{"collection_id": "lumen-developer-docs", "query": "What happens when I exceed the rate limit?"}'
```

Error responses: `401` missing/invalid key, `403` protected operation disabled (no `SERVICE_API_KEY`), `404`, `413` body too large,
`422` validation (`detail[]` with field locations), `429` rate limited (`Retry-After`), `503` dependency not configured or unreachable,
`504` timeout, `500` opaque with a `request_id` (details stay in server logs).

---

## Demo mode

`/demo/*` serves `demo/corpus/`: nine fictional documents ("Lumen Cloud", not a real company) shaped like a Document360 knowledge
base and a SharePoint library, including one ACL-restricted document that anonymous callers can never retrieve.

| Stage | Demo mode | Live mode |
|---|---|---|
| Chunking, fingerprints | production code | production code |
| Vector leg | hashed word + trigram embedding (stand-in) | OpenAI embeddings + pgvector HNSW |
| Keyword leg | Okapi BM25 in Python (stand-in) | PostgreSQL `ts_rank` over a GIN index |
| Fusion | production `_reciprocal_rank_fusion` | same |
| Rerank | not applied | cross-encoder |
| Answer | extractive sentences with citations, no LLM | LLM constrained to retrieved context |

Every demo response is labelled `"mode": "demo"`, and a failed live request returns an error; it never falls back to sample data.

---

## Security model

- **Secrets** live only in server environment variables; responses and the status endpoint expose booleans and sanitised error categories, never values.
- **Access**: `SERVICE_API_KEY` (constant-time compared) gates ingestion, feedback, debug and, by default, live queries. With no key configured those operations return 403.
- **ACL**: fail-closed SQL predicate; anonymous callers are public-only; client-supplied groups are ignored unless the caller holds the service key.
- **Abuse limits**: per-IP sliding-window rate limits (in-process; use a shared limiter or gateway for multi-instance deployments), 32 KB body limit, 30 s request timeout.
- **SSRF**: ingestion targets come only from server configuration; demo ingestion accepts only allow-listed collection IDs; Graph paging links are followed only on the Graph host.
- **Headers and CORS**: strict CSP, `nosniff`, `DENY` framing, `no-store`; CORS limited to `ALLOWED_ORIGINS`, no credentials.
- **Privacy**: request logs record method, path, status and latency only; question text is not logged or stored (feedback is keyed-only).

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
| `DATABASE_URL` | PostgreSQL connection string (asyncpg). Unset = live endpoints return 503; demo mode still works |
| `OPENAI_API_KEY` | Embeddings and LLM answer generation |
| `S3_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `S3_BUCKET_RAW`, `S3_BUCKET_ARTIFACTS` | Object storage (leave endpoint empty for AWS S3) |
| `DOCUMENT360_API_KEY`, `DOCUMENT360_PROJECT_ID`, `DOCUMENT360_BASE_URL` | Document360 connector |
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `SHAREPOINT_SITE_ID` | Microsoft Graph app registration (application permission `Sites.Read.All`, or `Sites.Selected` with a site grant) |
| `SERVICE_API_KEY` | Enables protected operations via the `X-API-Key` header |
| `ALLOW_ANONYMOUS_LIVE_QUERIES` | Default `false`: live `/retrieve` and `/ask` require the service key |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins (the frontend URL) |
| `RERANKER_ENABLED` | Set `false` where `requirements-rerank.txt` is not installed |
| `DEMO_MODE_ENABLED` | Default `true` |
| `TRUST_FORWARDED_FOR` | `true` only behind a proxy that sets `X-Forwarded-For` (e.g. Vercel) |
| `REQUIRE_DATABASE_ON_STARTUP` | `true` to fail fast when the database is unreachable |
| `EMBEDDING_MODEL` / `LLM_MODEL` | Defaults: `text-embedding-3-small` / `gpt-4o` |

Frontend (`web/`): `VITE_API_BASE_URL`, the public API URL.

---

## Deploying the demo (Vercel)

Two Vercel projects are built from this repository:

1. **API**: project root `.`, framework preset FastAPI (entrypoint `app.py`, configuration in `vercel.json`). Set `APP_ENV=production`,
   `RERANKER_ENABLED=false`, `TRUST_FORWARDED_FOR=true`, `ALLOWED_ORIGINS=https://<frontend-domain>`, plus any live-integration
   variables above.
2. **Frontend**: root directory `web`, framework preset Vite, Node 22. Set `VITE_API_BASE_URL=https://<api-domain>`.

Health and diagnostics: `GET /health/ready` and `GET /health/integrations`. For live retrieval, use a managed PostgreSQL with the
`vector` extension (for example Aurora PostgreSQL, Neon or Supabase) and run the full stack in the container from `Dockerfile`
when the cross-encoder reranker is required.

---

## License

MIT
