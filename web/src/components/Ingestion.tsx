import { useState } from "react";
import { call, type CollectionId, type Exchange, type SyncJob, type SyncScenario } from "../api";
import { CodeBlock, ErrorNotice, ExchangeInspector, Skeleton, StatusLine, shortHash } from "./common";

const SCENARIOS: { key: SyncScenario; label: string; description: string }[] = [
  { key: "upstream_edit", label: "Upstream edit", description: "One source document changed since the last sync. Only that document is re-chunked; the rest are skipped by fingerprint." },
  { key: "unchanged", label: "No changes", description: "Re-sync identical sources. Every document is skipped: no chunking, embedding or storage writes." },
  { key: "auth_failure", label: "Credential failure", description: "The connector's credentials are rejected. The job fails with an error and retry guidance." },
];

export function Ingestion() {
  const [collectionId, setCollectionId] = useState<CollectionId>("lumen-developer-docs");
  const [scenario, setScenario] = useState<SyncScenario>("upstream_edit");
  const [loading, setLoading] = useState(false);
  const [ex, setEx] = useState<Exchange<SyncJob> | null>(null);
  const [showHttp, setShowHttp] = useState(false);

  async function run() {
    setLoading(true);
    setEx(await call<SyncJob>("POST", "/demo/ingest", { collection_id: collectionId, scenario }));
    setLoading(false);
  }

  const job = ex?.data;
  return (
    <>
      <div className="page-head">
        <h1>Ingestion operations</h1>
        <p>
          Run a sync against an allow-listed sample source. The backend executes the production chunker and SHA-256 fingerprinting
          over bundled files in an isolated index, so you can see exactly what a real sync would skip, re-chunk and publish.
        </p>
      </div>

      <div className="notice" style={{ marginBottom: 14 }}>
        <div>
          <strong>Sandboxed.</strong> Connector HTTP calls are not made from this page and the shared demo index is not modified.
          Live syncs use <code>POST /ingest/document360</code> and <code>POST /ingest/sharepoint</code>, which require the server's
          service key and only target sources configured on the server.
        </div>
      </div>

      <div className="card">
        <div className="grid grid-2">
          <label className="field">
            Source
            <select value={collectionId} onChange={(e) => setCollectionId(e.target.value as CollectionId)}>
              <option value="lumen-developer-docs">Lumen Cloud Developer Docs (Document360-style)</option>
              <option value="lumen-it-operations">Lumen IT Operations Library (SharePoint-style)</option>
            </select>
          </label>
          <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
            <legend style={{ fontSize: "0.82rem", fontWeight: 600, color: "var(--text-2)", marginBottom: 4 }}>Scenario</legend>
            <div className="segmented">
              {SCENARIOS.map((s) => (
                <button key={s.key} type="button" aria-pressed={scenario === s.key} onClick={() => setScenario(s.key)}>{s.label}</button>
              ))}
            </div>
          </fieldset>
        </div>
        <p className="muted small" style={{ margin: "10px 0" }}>{SCENARIOS.find((s) => s.key === scenario)!.description}</p>
        <div className="row">
          <button type="button" className="btn btn-primary" onClick={run} disabled={loading}>{loading ? "Running…" : "Run sync"}</button>
        </div>
      </div>

      <section className="card" aria-live="polite" aria-busy={loading}>
        {!ex && !loading && <div className="empty">Choose a source and scenario, then run a sync.</div>}
        {loading && <Skeleton lines={4} />}
        {ex && !loading && (
          <div className="stack">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <StatusLine ex={ex} />
              <button type="button" className="btn btn-sm" onClick={() => setShowHttp((v) => !v)} aria-expanded={showHttp}>
                {showHttp ? "Hide" : "Show"} request & response
              </button>
            </div>
            {!ex.ok && <ErrorNotice message={ex.error ?? "Unknown error"} />}
            {job && (
              <>
                <div className="row">
                  <span className={`badge ${job.status === "completed" ? "connected" : "disconnected"}`}>Job {job.status}</span>
                  <span className="mono small muted">job_id {job.job_id}</span>
                  <span className="small muted">{job.duration_ms} ms</span>
                </div>
                <div className="status-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))" }}>
                  {[["Scanned", job.documents_scanned], ["Changed", job.documents_changed], ["Skipped (unchanged)", job.documents_skipped], ["Chunks created", job.chunks_created]]
                    .map(([label, value]) => (
                      <div key={label} className="status-card"><span className="muted small">{label}</span><strong style={{ fontSize: "1.4rem" }}>{value}</strong></div>
                    ))}
                </div>
                {job.status === "failed" && (
                  <div className="notice error">
                    <div>
                      <strong>{job.error_detail}</strong>
                      <ol className="small" style={{ margin: "8px 0 0", paddingLeft: 18 }}>
                        {job.retry_guidance.map((g) => <li key={g}>{g}</li>)}
                      </ol>
                    </div>
                  </div>
                )}
                {job.documents.length > 0 && (
                  <div className="table-wrap">
                    <table className="data">
                      <thead><tr><th>Document</th><th>Action</th><th>Published fingerprint</th><th>Incoming fingerprint</th><th>Chunks −/+</th></tr></thead>
                      <tbody>
                        {job.documents.map((d) => (
                          <tr key={d.document_id}>
                            <td>
                              <strong>{d.title}</strong>
                              {d.acl_groups.length > 0 && <div className="small muted">ACL: {d.acl_groups.join(", ")} (carried to every chunk)</div>}
                              {d.reason && <div className="small muted">{d.reason}</div>}
                            </td>
                            <td><span className={`badge plain ${d.action === "skipped" ? "unavailable" : "connected"}`}>{d.action}</span></td>
                            <td className="mono small" title={d.published_fingerprint}>{shortHash(d.published_fingerprint)}</td>
                            <td className="mono small" title={d.incoming_fingerprint}>{shortHash(d.incoming_fingerprint)}</td>
                            <td className="num">{d.chunks_deleted} / {d.chunks_inserted}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {job.documents.filter((d) => d.atomic_publish).map((d) => (
                  <div key={d.document_id} className="grid grid-2">
                    <div>
                      <h4>Atomic publish for “{d.title}”</h4>
                      <CodeBlock code={d.atomic_publish!.join("\n")} copy={false} />
                    </div>
                    <div>
                      <h4>New chunks</h4>
                      <table className="data">
                        <thead><tr><th>#</th><th>Type</th><th>Section path</th><th>Tokens</th></tr></thead>
                        <tbody>
                          {d.new_chunks!.map((c) => (
                            <tr key={c.chunk_index}><td className="num">{c.chunk_index}</td><td>{c.chunk_type}</td><td className="small">{c.section_path}</td><td className="num">{c.token_count}</td></tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ))}
                <p className="muted small">{job.note}</p>
              </>
            )}
            {showHttp && <ExchangeInspector ex={ex} />}
          </div>
        )}
      </section>

      <section className="section card">
        <h2>Why atomic publishing matters</h2>
        <p className="small">
          When a document is re-ingested, its old chunks are deleted and the new chunks inserted inside one database transaction
          (<code>ingestion/pipeline.py</code>). PostgreSQL readers see either the complete previous revision or the complete new one,
          so a query running mid-sync can never combine a stale paragraph with a fresh one from the same document. Raw files and chunk
          artifacts are written to object storage before the commit, and every run appends a row to <code>document_revisions</code>.
        </p>
        <p className="small">
          Fingerprinting happens before any processing: a SHA-256 of the raw source bytes. Unchanged documents skip chunking,
          embedding and storage writes entirely, which keeps incremental syncs cheap and makes retries after a failure idempotent.
        </p>
      </section>
    </>
  );
}
