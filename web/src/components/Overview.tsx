import type { Exchange, IntegrationStatus } from "../api";
import { ErrorNotice, Skeleton, StatusBadge } from "./common";
import type { Page } from "../App";

const FLOW = [
  {
    title: "Sources",
    items: ["Document360 REST API v2 (api_token header)", "Microsoft Graph v1.0 (OAuth2 client credentials)", "Per-document ACL groups captured"],
    src: "ingestion/connectors/",
  },
  {
    title: "Ingestion",
    items: ["SHA-256 fingerprint; unchanged docs skipped", "Structure-aware HTML chunking", "Batch embeddings; raw files to S3", "Atomic publish in one transaction"],
    src: "ingestion/pipeline.py",
  },
  {
    title: "Storage",
    items: ["PostgreSQL + pgvector (HNSW, cosine)", "GIN full-text index", "Revisions + ingestion job audit", "S3 / MinIO artifacts"],
    src: "core/models.py",
  },
  {
    title: "Retrieval",
    items: ["Vector + full-text in parallel", "Reciprocal Rank Fusion (k=60)", "Fail-closed ACL filter in SQL", "Cross-encoder rerank"],
    src: "retrieval/hybrid_retriever.py",
  },
  {
    title: "Grounded answers",
    items: ["Context-only prompting", "Numbered citations per chunk", "Feedback + debug traces"],
    src: "orchestrator/answer_engine.py",
  },
];

const MATRIX: { feature: string; live: string; demo: string }[] = [
  { feature: "Knowledge sources", live: "Document360 and SharePoint via their APIs", demo: "Bundled fictional HTML files shaped like both sources" },
  { feature: "Chunking and fingerprints", live: "Production chunker + SHA-256", demo: "Same production code" },
  { feature: "Vector search", live: "OpenAI embeddings + pgvector HNSW", demo: "Deterministic hashed n-gram embedding (stand-in)" },
  { feature: "Keyword search", live: "PostgreSQL ts_rank over GIN index", demo: "Okapi BM25 in Python (stand-in)" },
  { feature: "Rank fusion", live: "Reciprocal Rank Fusion", demo: "Same production function" },
  { feature: "Reranking", live: "Cross-encoder (ms-marco-MiniLM-L-6-v2)", demo: "Not applied" },
  { feature: "ACL filtering", live: "Fail-closed SQL predicate", demo: "Same rule; visitors are anonymous, public only" },
  { feature: "Answer generation", live: "LLM constrained to retrieved context", demo: "Extractive sentences with citations, no LLM" },
];

export function Overview({ status, go }: { status: Exchange<IntegrationStatus> | null; go: (p: Page) => void }) {
  const data = status?.data;
  return (
    <>
      <div className="page-head">
        <h1>Knowledge RAG API</h1>
        <p>
          A FastAPI backend that ingests technical documentation from <strong>Document360</strong> and{" "}
          <strong>SharePoint via Microsoft Graph</strong>, stores chunks and embeddings in <strong>PostgreSQL with pgvector</strong>,
          and serves hybrid retrieval and grounded answers with citations behind access controls.
        </p>
        <div className="row">
          <button type="button" className="btn btn-primary" onClick={() => go("playground")}>Ask the sample knowledge base</button>
          <button type="button" className="btn" onClick={() => go("integrations")}>How the integrations work</button>
          <button type="button" className="btn" onClick={() => go("reference")}>API reference</button>
        </div>
      </div>

      <section className="card" aria-labelledby="arch-h">
        <div className="card-title"><h2 id="arch-h">Architecture</h2><span className="muted small">Source file shown under each stage</span></div>
        <div className="flow">
          {FLOW.map((s) => (
            <div className="flow-step" key={s.title}>
              <div className="flow-box">
                <h3>{s.title}</h3>
                <ul>{s.items.map((i) => <li key={i}>{i}</li>)}</ul>
                <div className="src">{s.src}</div>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="section" aria-labelledby="status-h">
        <div className="card-title">
          <h2 id="status-h">Integration status on this deployment</h2>
          {data && (
            <span className="muted small">
              Checked {new Date(data.checked_at).toLocaleTimeString()}{data.cached ? ` (cached ${Math.round(data.cache_age_seconds)}s)` : ""}
            </span>
          )}
        </div>
        <p className="muted small" style={{ maxWidth: "80ch" }}>
          Each state comes from a live read-only request made by the backend (for example <code>SELECT 1</code>, an OAuth token
          request, or <code>HeadBucket</code>). A service is never shown as connected because it is merely configured.
        </p>
        {!status && <div className="card"><Skeleton lines={4} /></div>}
        {status && !status.ok && <ErrorNotice message={status.error ?? "Status unavailable."} />}
        {data && (
          <div className="status-grid">
            {data.integrations.map((i) => (
              <div className="status-card" key={i.key}>
                <div className="name"><span>{i.name}</span><StatusBadge state={i.state} /></div>
                <div className="detail">{i.detail}</div>
                {i.missing_config.length > 0 && <div className="meta">Needs: <span className="mono">{i.missing_config.join(", ")}</span></div>}
                {i.check && <div className="meta">Check: <span className="mono">{i.check}</span>{i.latency_ms != null ? ` · ${i.latency_ms} ms` : ""}</div>}
                {Object.keys(i.facts).length > 0 && (
                  <div className="meta">{Object.entries(i.facts).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : String(v)}`).join(" · ")}</div>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="section card" aria-labelledby="matrix-h">
        <div className="card-title">
          <h2 id="matrix-h">What is live and what is simulated</h2>
          {data && (
            <span className={`tag ${data.live_query_ready ? "live" : "demo"}`}>
              {data.live_query_ready ? "Live retrieval stack connected" : "Live retrieval stack not connected: demo mode"}
            </span>
          )}
        </div>
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Capability</th><th>Live mode (<code>/retrieve</code>, <code>/ask</code>, <code>/ingest</code>)</th><th>Demo mode (<code>/demo/*</code>)</th></tr></thead>
            <tbody>
              {MATRIX.map((m) => (
                <tr key={m.feature}><td><strong>{m.feature}</strong></td><td>{m.live}</td><td>{m.demo}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted small" style={{ marginTop: 10 }}>
          Demo responses are always labelled <code>"mode": "demo"</code>. A failed live request returns an error; it never falls back to sample data.
        </p>
      </section>
    </>
  );
}
