import { useEffect, useMemo, useState } from "react";
import {
  API_BASE,
  call,
  curlFor,
  type Exchange,
  type IntegrationStatus,
  type OpenAPIOperation,
  type OpenAPISchema,
  type OpenAPISpec,
} from "../api";
import { CodeBlock, ErrorNotice, ExchangeInspector, Skeleton } from "./common";

type Access = "public" | "key" | "key-unless-open";

function accessFor(path: string): Access {
  if (path.startsWith("/health") || path.startsWith("/demo")) return "public";
  if (path === "/retrieve/" || path === "/ask/") return "key-unless-open";
  return "key";
}

// Example bodies for each POST endpoint. Field names match the Pydantic models
// (they are validated against the live schema below before display).
const EXAMPLES: Record<string, unknown> = {
  "/demo/ask": { collection_id: "lumen-developer-docs", query: "What happens when I exceed the rate limit?", top_k: 5, retrieval_mode: "hybrid" },
  "/demo/retrieve": { collection_id: "lumen-it-operations", query: "lost laptop", top_k: 5, retrieval_mode: "keyword" },
  "/demo/ingest": { collection_id: "lumen-developer-docs", scenario: "upstream_edit" },
  "/retrieve/": { query: "How do I configure SSO?", top_k: 5, diagnostics: true },
  "/ask/": { query: "What are the rate limits for the REST API?", top_k: 5 },
  "/debug/trace": { query: "How do I configure SSO?", acl_groups: ["engineering"], top_k: 5 },
  "/feedback/": { query: "How do I reset my password?", rating: "negative", failure_category: "wrong_answer", comment: "Answer covered API keys, not passwords", chunk_ids: [] },
};
const TRY_GET: Record<string, string> = { "/demo/documents/{document_id}": "/demo/documents/d360-webhooks", "/ingest/jobs/{job_id}": "" };

const ERRORS: [string, string][] = [
  ["401", "Missing or invalid X-API-Key on a protected endpoint (or a live query when anonymous live queries are disabled)."],
  ["403", "Protected operation disabled because the server has no SERVICE_API_KEY configured."],
  ["404", "Unknown resource, or a restricted document requested anonymously (no existence leak)."],
  ["413", "Request body larger than MAX_REQUEST_BYTES (32 KB default)."],
  ["422", "Validation error: FastAPI returns detail[] with the failing field location and message."],
  ["429", "Per-IP rate limit exceeded; honour the Retry-After header."],
  ["503", "A required dependency is not configured or unreachable (database, embeddings, LLM, connector)."],
  ["504", "Request exceeded REQUEST_TIMEOUT_SECONDS."],
  ["500", "Unexpected error. The body carries only a request_id; details stay in server logs."],
];

function resolve(schema: OpenAPISchema | undefined, spec: OpenAPISpec): OpenAPISchema | undefined {
  if (!schema?.$ref) return schema;
  const name = schema.$ref.split("/").pop()!;
  return spec.components?.schemas?.[name];
}

function typeOf(s: OpenAPISchema | undefined, spec: OpenAPISpec): string {
  const r = resolve(s, spec);
  if (!r) return "any";
  if (r.enum) return r.enum.map((e) => JSON.stringify(e)).join(" | ");
  if (r.anyOf) return r.anyOf.map((a) => typeOf(a, spec)).join(" | ");
  if (r.type === "array") return `${typeOf(r.items, spec)}[]`;
  return r.type ?? r.title ?? "object";
}

function constraints(s: OpenAPISchema | undefined, spec: OpenAPISpec): string {
  const r = resolve(s, spec);
  const inner = r?.anyOf?.find((a) => a.type !== "null") ?? r;
  if (!inner) return "";
  const c: string[] = [];
  if (inner.minLength != null) c.push(`min length ${inner.minLength}`);
  if (inner.maxLength != null) c.push(`max length ${inner.maxLength}`);
  if (inner.minimum != null) c.push(`≥ ${inner.minimum}`);
  if (inner.maximum != null) c.push(`≤ ${inner.maximum}`);
  if (inner.maxItems != null) c.push(`max ${inner.maxItems} items`);
  return c.join(", ");
}

function Endpoint({ method, path, op, spec, anonymousLive }: {
  method: string; path: string; op: OpenAPIOperation; spec: OpenAPISpec; anonymousLive: boolean | undefined;
}) {
  const [open, setOpen] = useState(false);
  const [ex, setEx] = useState<Exchange<unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  const access = accessFor(path);
  const body = resolve(op.requestBody?.content?.["application/json"]?.schema, spec);
  const example = EXAMPLES[path];
  const exampleValid = !example || !body?.properties ||
    Object.keys(example as object).every((k) => k in (body.properties ?? {}));
  const tryPath = method === "get" ? (TRY_GET[path] ?? (path.includes("{") ? "" : path)) : path;
  const canTry = access === "public" && tryPath !== "";
  const curlBody = method === "post" ? (example ?? {}) : undefined;
  const authHeader = access === "public" ? "" : " \\\n  -H 'X-API-Key: $SERVICE_API_KEY'";

  return (
    <div className="endpoint">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className={`method ${method}`}>{method.toUpperCase()}</span>
        <span className="path">{path}</span>
        <span className="muted small" style={{ flex: 1 }}>{op.summary}</span>
        <span className={`tag ${access === "public" ? "live" : ""}`}>
          {access === "public" ? "public" : access === "key" ? "X-API-Key" : anonymousLive ? "public (enabled)" : "X-API-Key"}
        </span>
      </button>
      {open && (
        <div className="body stack">
          {op.description && <p className="small" style={{ whiteSpace: "pre-line", marginTop: 10 }}>{op.description.trim()}</p>}
          {access === "key-unless-open" && (
            <p className="small muted">
              Requires X-API-Key unless the deployment sets ALLOW_ANONYMOUS_LIVE_QUERIES=true. Anonymous callers are always restricted to
              public chunks; <code>acl_groups</code> is honoured only for keyed service callers.
            </p>
          )}
          {op.parameters && op.parameters.length > 0 && (
            <div>
              <h4>Parameters</h4>
              <table className="data"><tbody>
                {op.parameters.map((p) => (
                  <tr key={p.name}><td className="mono">{p.name}</td><td>{p.in}</td><td className="mono small">{typeOf(p.schema, spec)}</td><td>{p.required ? "required" : "optional"}</td></tr>
                ))}
              </tbody></table>
            </div>
          )}
          {body?.properties && (
            <div>
              <h4>Request body ({body.title})</h4>
              <div className="table-wrap">
                <table className="data">
                  <thead><tr><th>Field</th><th>Type</th><th>Required</th><th>Constraints / default</th></tr></thead>
                  <tbody>
                    {Object.entries(body.properties).map(([name, s]) => (
                      <tr key={name}>
                        <td className="mono">{name}</td>
                        <td className="mono small">{typeOf(s, spec)}</td>
                        <td>{body.required?.includes(name) ? "yes" : "no"}</td>
                        <td className="small">
                          {[constraints(s, spec), s.default !== undefined ? `default ${JSON.stringify(s.default)}` : "", s.description ?? ""].filter(Boolean).join(" · ")}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          <div>
            <h4>Example</h4>
            {!exampleValid && <p className="field-error small">Example out of date with the live schema.</p>}
            <CodeBlock code={curlFor(method.toUpperCase(), tryPath || path, curlBody) + authHeader} />
          </div>
          {op.responses && (
            <p className="small muted">Responses: {Object.entries(op.responses).map(([c, r]) => `${c} ${r.description ?? ""}`).join(" · ")}</p>
          )}
          {canTry && (
            <div>
              <button type="button" className="btn btn-sm" disabled={busy} onClick={async () => {
                setBusy(true);
                setEx(await call(method === "get" ? "GET" : "POST", tryPath, method === "post" ? example : undefined));
                setBusy(false);
              }}>{busy ? "Sending…" : "Send example request"}</button>
              {ex && <div style={{ marginTop: 10 }}><ExchangeInspector ex={ex} /></div>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function ApiReference({ status }: { status: Exchange<IntegrationStatus> | null }) {
  const [spec, setSpec] = useState<Exchange<OpenAPISpec> | null>(null);
  useEffect(() => {
    call<OpenAPISpec>("GET", "/openapi.json").then(setSpec);
  }, []);

  const groups = useMemo(() => {
    const out = new Map<string, { method: string; path: string; op: OpenAPIOperation }[]>();
    for (const [path, ops] of Object.entries(spec?.data?.paths ?? {})) {
      for (const [method, op] of Object.entries(ops)) {
        const tag = op.tags?.[0] ?? "Other";
        if (!out.has(tag)) out.set(tag, []);
        out.get(tag)!.push({ method, path, op });
      }
    }
    return [...out.entries()];
  }, [spec]);

  return (
    <>
      <div className="page-head">
        <h1>API reference</h1>
        <p>
          Generated at runtime from the backend's OpenAPI document (<code>GET /openapi.json</code>), so field names, types and limits are
          exactly what the server validates. Public endpoints can be called from this page.
        </p>
        <div className="row">
          <a className="btn" href={`${API_BASE}/docs`} target="_blank" rel="noreferrer">Swagger UI</a>
          <a className="btn" href={`${API_BASE}/redoc`} target="_blank" rel="noreferrer">ReDoc</a>
          <a className="btn" href={`${API_BASE}/openapi.json`} target="_blank" rel="noreferrer">openapi.json</a>
        </div>
      </div>

      <div className="card">
        <h2>Authentication</h2>
        <p className="small">
          Protected endpoints take a server-issued key in the <code>X-API-Key</code> header. It is intended for trusted backends and
          gateways, never for browsers. No endpoint accepts cookies, and CORS is limited to the configured frontend origins.
        </p>
        <h3>Error responses</h3>
        <div className="table-wrap">
          <table className="data"><tbody>
            {ERRORS.map(([code, text]) => <tr key={code}><td className="mono">{code}</td><td>{text}</td></tr>)}
          </tbody></table>
        </div>
      </div>

      <div className="section">
        {!spec && <div className="card"><Skeleton lines={6} /></div>}
        {spec && !spec.ok && <ErrorNotice message={spec.error ?? "Could not load the OpenAPI document."} />}
        {spec?.data && groups.map(([tag, ops]) => (
          <section key={tag} className="section" aria-label={tag}>
            <h2>{tag}</h2>
            {ops.map((o) => (
              <Endpoint key={o.method + o.path} {...o} spec={spec.data!} anonymousLive={status?.data?.access.anonymous_live_queries} />
            ))}
          </section>
        ))}
      </div>
    </>
  );
}
