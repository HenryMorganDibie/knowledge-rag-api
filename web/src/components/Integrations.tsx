import { useState } from "react";
import type { CollectionsResponse, Exchange, Integration, IntegrationStatus } from "../api";
import { CodeBlock, StatusBadge, Tabs, shortHash } from "./common";

type Key = "document360" | "graph" | "storage";

function LiveStatus({ status, keys }: { status: Exchange<IntegrationStatus> | null; keys: string[] }) {
  const items = keys
    .map((k) => status?.data?.integrations.find((i) => i.key === k))
    .filter((i): i is Integration => !!i);
  if (!status) return <StatusBadge state="checking" />;
  if (!items.length) return <span className="muted small">Status unavailable</span>;
  return (
    <div className="stack" style={{ gap: 6 }}>
      {items.map((i) => (
        <div key={i.key} className="row small">
          <StatusBadge state={i.state} /> <strong>{i.name}</strong> <span className="muted">{i.detail}</span>
        </div>
      ))}
    </div>
  );
}

function ConfigTable({ rows, status }: { rows: { name: string; purpose: string; secret: boolean }[]; status: Integration | undefined }) {
  return (
    <div className="table-wrap">
      <table className="data">
        <thead><tr><th>Environment variable</th><th>Purpose</th><th>On this deployment</th></tr></thead>
        <tbody>
          {rows.map((r) => {
            const missing = status?.missing_config.includes(r.name);
            return (
              <tr key={r.name}>
                <td className="mono">{r.name}{r.secret && <span className="tag" style={{ marginLeft: 6 }}>secret</span>}</td>
                <td>{r.purpose}</td>
                <td>{status ? (missing ? <span className="muted">not set</span> : "set (value never exposed)") : "…"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Steps({ steps }: { steps: { title: string; body: string; code?: string }[] }) {
  return (
    <ol className="stack" style={{ paddingLeft: 18, gap: 12 }}>
      {steps.map((s) => (
        <li key={s.title}>
          <strong>{s.title}</strong>
          <p className="small" style={{ color: "var(--text-2)", margin: "2px 0 6px" }}>{s.body}</p>
          {s.code && <CodeBlock code={s.code} />}
        </li>
      ))}
    </ol>
  );
}

const SERVER_ONLY = (
  <div className="notice warn small">
    <div>
      <strong>Credentials are configured on the server only.</strong> This interface intentionally has no fields for API tokens,
      client secrets or passwords. Set them as environment variables on the API host (or a secrets manager); the browser
      only ever sees the resulting status.
    </div>
  </div>
);

function Document360({ status }: { status: Exchange<IntegrationStatus> | null }) {
  const st = status?.data?.integrations.find((i) => i.key === "document360");
  return (
    <div className="stack">
      <div className="card">
        <div className="card-title"><h2>Document360 connector</h2><span className="mono small muted">ingestion/connectors/document360.py</span></div>
        <LiveStatus status={status} keys={["document360"]} />
        <p style={{ marginTop: 12 }}>
          Pulls every article in a project version over the Document360 REST API, fingerprints the raw HTML, and hands changed
          articles to the ingestion pipeline. Article access groups are carried through to every chunk as ACL metadata.
        </p>
        <h3>Authentication</h3>
        <p className="small">
          A project API token sent in the <code>api_token</code> request header. The token is read from{" "}
          <code>DOCUMENT360_API_KEY</code> on the server and is never sent to the browser.
        </p>
        <h3>Request flow</h3>
        <Steps steps={[
          { title: "List articles, page by page", body: "pageSize 50; the loop stops at the first empty page.",
            code: "GET {DOCUMENT360_BASE_URL}/articles?projectVersionId={DOCUMENT360_PROJECT_ID}&page=1&pageSize=50\napi_token: <server-side secret>" },
          { title: "Fetch each article", body: "Fields used: title, html_content, public_url, category_name, sub_category_name, access_groups.",
            code: "GET {DOCUMENT360_BASE_URL}/articles/{article_id}" },
          { title: "Fingerprint and compare", body: "SHA-256 of the raw HTML. If it matches the stored fingerprint, the article is skipped without re-chunking or re-embedding." },
          { title: "Chunk, embed, publish", body: "Structure-aware chunks keep the heading path (e.g. \"Developer Guide > Security > Scopes\"); old chunks are replaced in one transaction." },
        ]} />
        <p className="muted small">
          The endpoint paths above are exactly what the connector code requests. They have not been exercised against a live
          Document360 tenant from this deployment; the status check above uses the same list request with <code>pageSize=1</code>.
        </p>
      </div>
      <div className="card">
        <h3>Configuration</h3>
        <ConfigTable status={st} rows={[
          { name: "DOCUMENT360_API_KEY", purpose: "Project API token (api_token header)", secret: true },
          { name: "DOCUMENT360_PROJECT_ID", purpose: "Project version ID passed as projectVersionId", secret: false },
          { name: "DOCUMENT360_BASE_URL", purpose: "Defaults to https://apihub.document360.io/v2", secret: false },
        ]} />
        <p className="small" style={{ marginTop: 10 }}>
          Trigger a sync (server-to-server, requires the service key): <code>POST /ingest/document360</code> with <code>X-API-Key</code>,
          then poll <code>GET /ingest/jobs/{"{job_id}"}</code>.
        </p>
        {SERVER_ONLY}
      </div>
    </div>
  );
}

function Graph({ status }: { status: Exchange<IntegrationStatus> | null }) {
  const st = status?.data?.integrations.find((i) => i.key === "microsoft_graph");
  return (
    <div className="stack">
      <div className="card">
        <div className="card-title"><h2>Microsoft Graph / SharePoint connector</h2><span className="mono small muted">ingestion/connectors/sharepoint.py</span></div>
        <LiveStatus status={status} keys={["microsoft_graph"]} />
        <p style={{ marginTop: 12 }}>
          Walks a SharePoint document library through Microsoft Graph, downloads supported files (PDF, DOCX, HTML, TXT), extracts
          text, and records the groups each file is shared with so retrieval can enforce them.
        </p>
        <h3>Authentication: OAuth 2.0 client credentials</h3>
        <p className="small">
          The API authenticates as an Entra ID app registration (no user sign-in, no delegated permissions). Tokens are cached in
          memory and refreshed two minutes before expiry.
        </p>
        <Steps steps={[
          { title: "Acquire an app-only token", body: "Microsoft identity platform v2 endpoint for the configured tenant.",
            code: "POST https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/v2.0/token\nContent-Type: application/x-www-form-urlencoded\n\ngrant_type=client_credentials\n&client_id={AZURE_CLIENT_ID}\n&client_secret=<server-side secret>\n&scope=https://graph.microsoft.com/.default" },
          { title: "Enumerate the drive", body: "Folders are walked recursively; every listing follows @odata.nextLink, and only links on the Graph host are followed.",
            code: "GET https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_SITE_ID}/drive/root/children\nGET https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_SITE_ID}/drive/items/{folder_id}/children\nAuthorization: Bearer <token>" },
          { title: "Download and extract", body: "Uses the item's short-lived @microsoft.graph.downloadUrl; text extracted with pdfplumber, docx2txt or BeautifulSoup." },
          { title: "Capture permissions as ACL groups", body: "Group display names from grantedToV2 / grantedTo become the file's acl_groups.",
            code: "GET https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_SITE_ID}/drive/items/{item_id}/permissions" },
          { title: "Fingerprint, chunk, embed, publish", body: "Same pipeline and atomic publish as Document360." },
        ]} />
      </div>
      <div className="card">
        <h3>Required app registration</h3>
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Microsoft Graph application permission</th><th>Why</th></tr></thead>
            <tbody>
              <tr><td className="mono">Sites.Read.All</td><td>Read the site, drive items, file content and item permissions. Requires admin consent.</td></tr>
              <tr><td className="mono">Sites.Selected</td><td>Least-privilege alternative: grant read on the single site via the Graph site permissions API instead of tenant-wide access.</td></tr>
            </tbody>
          </table>
        </div>
        <h3 style={{ marginTop: 14 }}>Configuration</h3>
        <ConfigTable status={st} rows={[
          { name: "AZURE_TENANT_ID", purpose: "Directory (tenant) ID", secret: false },
          { name: "AZURE_CLIENT_ID", purpose: "Application (client) ID", secret: false },
          { name: "AZURE_CLIENT_SECRET", purpose: "Client secret (or rotate to a certificate credential)", secret: true },
          { name: "SHAREPOINT_SITE_ID", purpose: "Graph site ID, e.g. contoso.sharepoint.com,{site-guid},{web-guid}", secret: false },
        ]} />
        <p className="small" style={{ marginTop: 10 }}>
          Trigger a sync: <code>POST /ingest/sharepoint</code> with <code>X-API-Key</code>. The site is fixed by server configuration;
          callers cannot point the connector at another host or site.
        </p>
        {SERVER_ONLY}
      </div>
    </div>
  );
}

function Storage({ status, collections }: { status: Exchange<IntegrationStatus> | null; collections: Exchange<CollectionsResponse> | null }) {
  return (
    <div className="stack">
      <div className="card">
        <div className="card-title"><h2>Storage and retrieval</h2><span className="mono small muted">core/models.py · retrieval/hybrid_retriever.py</span></div>
        <LiveStatus status={status} keys={["postgres", "pgvector", "embeddings", "object_storage"]} />
        <div className="grid grid-2" style={{ marginTop: 14 }}>
          <div>
            <h3>From document to chunk</h3>
            <ul className="small" style={{ paddingLeft: 18, color: "var(--text-2)" }}>
              <li><strong>document_sources</strong>: one row per source document with its fingerprint and ACL groups.</li>
              <li><strong>document_revisions</strong>: immutable history; every republish appends a revision with its S3 key.</li>
              <li><strong>document_chunks</strong>: content, section path, chunk type (text, table, image description), embedding, ACL groups.</li>
              <li><strong>ingestion_jobs</strong>: status and counters for every sync run.</li>
            </ul>
            <h3>Indexes</h3>
            <CodeBlock code={"CREATE INDEX idx_chunks_embedding_hnsw ON document_chunks\n  USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);\nCREATE INDEX idx_chunks_fts ON document_chunks\n  USING gin (to_tsvector('english', content));"} />
          </div>
          <div>
            <h3>Query path</h3>
            <ol className="small" style={{ paddingLeft: 18, color: "var(--text-2)" }}>
              <li>Embed the query; cosine search over the HNSW index (top 20).</li>
              <li>Full-text search with <code>plainto_tsquery</code> and <code>ts_rank</code> (top 20).</li>
              <li>Merge with Reciprocal Rank Fusion: <code>score = Σ 1 / (60 + rank)</code>; passages found by both lists rise.</li>
              <li>Cross-encoder rerank of the top candidates for final precision.</li>
            </ol>
            <h3>ACL filter (fails closed)</h3>
            <p className="small">Applied inside both SQL queries, so restricted rows never leave the database:</p>
            <CodeBlock code={"-- anonymous caller\nAND acl_groups::jsonb = '[]'::jsonb\n-- authenticated service caller asserting groups\nAND (acl_groups::jsonb = '[]'::jsonb OR acl_groups::jsonb ?| :acl_groups)"} />
            <p className="small muted">
              Groups are accepted only from callers holding the service key (a trusted gateway that has authenticated the user).
              Anonymous requests are public-only regardless of the <code>acl_groups</code> they send.
            </p>
          </div>
        </div>
      </div>
      <div className="card">
        <div className="card-title"><h3>Indexed sample documents (computed by the backend)</h3><span className="tag demo">demo data</span></div>
        {!collections?.data ? <p className="muted small">{collections?.error ?? "Loading…"}</p> : (
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>Document</th><th>Collection</th><th>SHA-256 fingerprint</th><th>Chunks</th></tr></thead>
              <tbody>
                {collections.data.collections.flatMap((c) => c.documents.map((d) => (
                  <tr key={d.id}>
                    <td><strong>{d.title}</strong><div className="muted small">{d.category_path}</div></td>
                    <td><span className="tag">{c.source_type}</span></td>
                    <td className="mono small" title={d.fingerprint}>{shortHash(d.fingerprint, 16)}</td>
                    <td className="num">{d.chunk_count}</td>
                  </tr>
                )))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

export function Integrations({ status, collections }: {
  status: Exchange<IntegrationStatus> | null;
  collections: Exchange<CollectionsResponse> | null;
}) {
  const [tab, setTab] = useState<Key>("document360");
  return (
    <>
      <div className="page-head">
        <h1>Integration explorer</h1>
        <p>
          How each external system is authenticated, which API calls are made, what permissions are required, and the live status of
          each integration on this deployment.
        </p>
      </div>
      <Tabs<Key>
        label="Integrations"
        value={tab}
        onChange={setTab}
        tabs={[
          { key: "document360", label: "Document360" },
          { key: "graph", label: "Microsoft Graph / SharePoint" },
          { key: "storage", label: "Storage & retrieval" },
        ]}
      />
      {tab === "document360" && <Document360 status={status} />}
      {tab === "graph" && <Graph status={status} />}
      {tab === "storage" && <Storage status={status} collections={collections} />}
    </>
  );
}
