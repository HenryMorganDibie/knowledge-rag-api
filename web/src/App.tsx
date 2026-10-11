import { useCallback, useEffect, useState } from "react";
import { API_BASE, API_CONFIGURED, call, type CollectionsResponse, type Exchange, type IntegrationStatus } from "./api";
import { Overview } from "./components/Overview";
import { Playground } from "./components/Playground";
import { Integrations } from "./components/Integrations";
import { Ingestion } from "./components/Ingestion";
import { ApiReference } from "./components/ApiReference";

export type Page = "overview" | "playground" | "integrations" | "ingestion" | "reference";

const PAGES: { key: Page; label: string; hint: string }[] = [
  { key: "overview", label: "Overview", hint: "Architecture and live status" },
  { key: "playground", label: "Playground", hint: "Ask, inspect, compare" },
  { key: "integrations", label: "Integrations", hint: "Document360, Graph, storage" },
  { key: "ingestion", label: "Ingestion", hint: "Sync jobs and fingerprints" },
  { key: "reference", label: "API reference", hint: "Endpoints from OpenAPI" },
];

function pageFromHash(): Page {
  const h = window.location.hash.replace("#", "") as Page;
  return PAGES.some((p) => p.key === h) ? h : "overview";
}

export default function App() {
  const [page, setPage] = useState<Page>(pageFromHash);
  const [status, setStatus] = useState<Exchange<IntegrationStatus> | null>(null);
  const [collections, setCollections] = useState<Exchange<CollectionsResponse> | null>(null);

  useEffect(() => {
    const onHash = () => setPage(pageFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    call<IntegrationStatus>("GET", "/health/integrations").then(setStatus);
    call<CollectionsResponse>("GET", "/demo/collections").then(setCollections);
  }, []);

  const go = useCallback((p: Page) => {
    window.location.hash = p;
    window.scrollTo({ top: 0 });
  }, []);

  const apiState = !status ? "checking" : status.ok ? "connected" : "disconnected";

  return (
    <>
      <header className="topbar">
        <div className="brand">
          <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="7" fill="#1e3a8a" /><path d="M9 10h9a4 4 0 0 1 0 8h-4l6 5" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" /><path d="M9 10v13" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" /></svg>
          Knowledge RAG API
          <span className="tag demo">Demo</span>
        </div>
        <span className="spacer" />
        <span className="api-host mono" title={API_BASE}>{API_BASE || "API URL not configured"}</span>
        <span className={`badge ${apiState}`} role="status">{apiState === "checking" ? "Checking API" : apiState === "connected" ? "API online" : "API unreachable"}</span>
      </header>
      <div className="layout">
        <aside className="sidenav">
          <nav aria-label="Sections">
            {PAGES.map((p) => (
              <button key={p.key} type="button" className="navbtn" aria-current={page === p.key ? "page" : undefined} onClick={() => go(p.key)}>
                <span>{p.label}<small>{p.hint}</small></span>
              </button>
            ))}
          </nav>
        </aside>
        <main>
          {!API_CONFIGURED && (
            <div className="notice error" role="alert" style={{ marginBottom: 16 }}>
              <div><strong>Deployment misconfigured.</strong> This build has no <code>VITE_API_BASE_URL</code>, so it cannot reach the API.</div>
            </div>
          )}
          {API_CONFIGURED && status && !status.ok && (
            <div className="notice error" role="alert" style={{ marginBottom: 16 }}>
              <div>
                <strong>The API is not reachable.</strong> {status.error} Hosted backends on free tiers can take up to a minute to wake;
                reload shortly. <span className="mono small">{API_BASE}/health/</span>
              </div>
            </div>
          )}
          <div className="notice" style={{ marginBottom: 18 }}>
            <div>
              <strong>Public demo.</strong> Questions are answered from a bundled, fictional sample knowledge base (“Lumen Cloud”) using
              the production chunking, fingerprinting and rank-fusion code, with deterministic stand-ins for embeddings and the LLM.
              Integration status reflects real checks made by the server. No credentials are needed and questions are not stored.
            </div>
          </div>
          {page === "overview" && <Overview status={status} go={go} />}
          {page === "playground" && <Playground collections={collections} />}
          {page === "integrations" && <Integrations status={status} collections={collections} />}
          {page === "ingestion" && <Ingestion />}
          {page === "reference" && <ApiReference status={status} />}
          <footer className="foot">
            FastAPI · PostgreSQL + pgvector · Document360 API · Microsoft Graph · S3. Sample content is fictional.
          </footer>
        </main>
      </div>
    </>
  );
}
