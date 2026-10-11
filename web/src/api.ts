// Typed client for the Knowledge RAG API. Types mirror the backend Pydantic models
// in api/routes/demo.py and the dict shapes returned by core/integrations.py.

const configured = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim();
export const API_BASE: string = (configured || (import.meta.env.DEV ? "http://localhost:8000" : "")).replace(/\/$/, "");
export const API_CONFIGURED = API_BASE !== "";

export type CollectionId = "lumen-developer-docs" | "lumen-it-operations";
export type RetrievalMode = "hybrid" | "vector" | "keyword";
export type SyncScenario = "unchanged" | "upstream_edit" | "auth_failure";
export type IntegrationState = "connected" | "disconnected" | "unavailable" | "demo";

export interface Integration {
  key: string;
  name: string;
  state: IntegrationState;
  detail: string;
  check: string | null;
  missing_config: string[];
  latency_ms: number | null;
  facts: Record<string, unknown>;
}

export interface IntegrationStatus {
  checked_at: string;
  live_query_ready: boolean;
  demo_mode: boolean;
  integrations: Integration[];
  cached: boolean;
  cache_age_seconds: number;
  access: { anonymous_live_queries: boolean; protected_operations_enabled: boolean };
}

export interface DemoDocumentSummary {
  id: string;
  title: string;
  category_path: string;
  updated_at: string;
  fingerprint: string;
  chunk_count: number;
}

export interface DemoCollection {
  id: CollectionId;
  name: string;
  source_type: "document360" | "sharepoint";
  description: string;
  simulated_origin: string;
  sample_questions: string[];
  documents: DemoDocumentSummary[];
  restricted_documents_hidden: number;
  chunk_count: number;
}

export interface CollectionsResponse {
  mode: "demo";
  notice: string;
  indexed_at: string;
  collections: DemoCollection[];
}

export interface RetrievedChunk {
  chunk_id: string;
  source_id: string;
  content: string;
  section_path: string;
  heading: string;
  chunk_type: string;
  vector_score: number | null;
  rrf_score: number | null;
  rerank_score: number | null;
  document_title: string;
  category_path: string;
  source_type: string;
  vector_rank: number | null;
  bm25_rank: number | null;
  bm25_score: number | null;
}

export interface RetrievalDiagnostics {
  vector_hits: number;
  bm25_hits: number;
  after_rrf: number | null;
  after_rerank: number | null;
  rerank_applied: boolean;
  acl_filter_applied: boolean;
  acl_public_only: boolean;
  acl_excluded_chunks: number;
  chunks_searched: number;
  query_terms: string[];
  top_rrf_scores: { chunk_id: string; rrf_score: number }[];
  latency_ms: number;
  implementation: Record<string, string>;
}

export interface RetrievalResponse {
  mode: "demo";
  retrieval_mode: RetrievalMode;
  collection_id: CollectionId;
  query: string;
  chunks: RetrievedChunk[];
  total_retrieved: number;
  diagnostics: RetrievalDiagnostics;
}

export interface Citation {
  index: number;
  chunk_id: string;
  document_id: string;
  document_title: string;
  source_type: string;
  section_path: string;
  heading: string;
  excerpt: string;
}

export interface AnswerResponse {
  mode: "demo";
  answer: string;
  grounded: boolean;
  citations: Citation[];
  chunks_used: number;
  model: string;
  retrieval: RetrievalResponse;
  latency_ms: number;
}

export interface DocumentChunk {
  chunk_id: string;
  chunk_index: number;
  chunk_type: string;
  section_path: string;
  heading: string;
  token_count: number;
  content: string;
}

export interface DocumentResponse {
  mode: "demo";
  id: string;
  collection_id: CollectionId;
  title: string;
  source_type: string;
  category_path: string;
  updated_at: string;
  fingerprint: string;
  acl_groups: string[];
  chunks: DocumentChunk[];
}

export interface SyncDocument {
  document_id: string;
  title: string;
  acl_groups: string[];
  published_fingerprint: string;
  incoming_fingerprint: string;
  action: "skipped" | "republished";
  reason: string | null;
  revision: number;
  chunks_deleted: number;
  chunks_inserted: number;
  new_chunks?: { chunk_index: number; chunk_type: string; section_path: string; token_count: number }[];
  atomic_publish?: string[];
}

export interface SyncJob {
  job_id: string;
  mode: "demo";
  collection_id: CollectionId;
  source_type: "document360" | "sharepoint";
  scenario: SyncScenario;
  status: "completed" | "failed";
  started_at: string;
  completed_at: string;
  duration_ms: number;
  sandboxed: boolean;
  note: string;
  documents_scanned: number;
  documents_changed: number;
  documents_skipped: number;
  chunks_created: number;
  error_detail: string | null;
  retry_guidance: string[];
  documents: SyncDocument[];
}

export interface OpenAPISpec {
  info: { title: string; version: string; description?: string };
  paths: Record<string, Record<string, OpenAPIOperation>>;
  components?: { schemas?: Record<string, OpenAPISchema> };
}

export interface OpenAPIOperation {
  tags?: string[];
  summary?: string;
  description?: string;
  operationId?: string;
  parameters?: { name: string; in: string; required?: boolean; schema?: OpenAPISchema }[];
  requestBody?: { content: Record<string, { schema: OpenAPISchema }>; required?: boolean };
  responses?: Record<string, { description?: string }>;
}

export interface OpenAPISchema {
  $ref?: string;
  type?: string;
  title?: string;
  description?: string;
  enum?: unknown[];
  const?: unknown;
  default?: unknown;
  properties?: Record<string, OpenAPISchema>;
  required?: string[];
  items?: OpenAPISchema;
  anyOf?: OpenAPISchema[];
  minLength?: number;
  maxLength?: number;
  minimum?: number;
  maximum?: number;
  maxItems?: number;
}

/** Everything the request inspector shows about one HTTP exchange. */
export interface Exchange<T> {
  method: "GET" | "POST";
  path: string;
  url: string;
  requestBody?: unknown;
  status: number | null; // null = network failure, no HTTP response
  ok: boolean;
  latencyMs: number;
  requestId: string | null;
  data: T | null;
  raw: unknown;
  error: string | null;
}

function describeError(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc: [...], msg}]
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).filter((p) => p !== "body").join(".")}: ${d.msg}`)
      .join("; ");
  }
  if (typeof detail === "string") return detail;
  if (status === 429) return "Rate limit exceeded. Wait a moment and try again.";
  return `Request failed with HTTP ${status}.`;
}

export async function call<T>(method: "GET" | "POST", path: string, body?: unknown, signal?: AbortSignal): Promise<Exchange<T>> {
  const url = `${API_BASE}${path}`;
  const started = performance.now();
  const base = { method, path, url, requestBody: body };
  if (!API_CONFIGURED) {
    return { ...base, status: null, ok: false, latencyMs: 0, requestId: null, data: null, raw: null,
      error: "This build has no API URL configured (VITE_API_BASE_URL)." };
  }
  try {
    const res = await fetch(url, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
    const latencyMs = Math.round(performance.now() - started);
    let raw: unknown = null;
    const text = await res.text();
    try {
      raw = text ? JSON.parse(text) : null;
    } catch {
      raw = text;
    }
    return {
      ...base,
      status: res.status,
      ok: res.ok,
      latencyMs,
      requestId: res.headers.get("x-request-id"),
      data: res.ok ? (raw as T) : null,
      raw,
      error: res.ok ? null : describeError(res.status, raw),
    };
  } catch (e) {
    const aborted = e instanceof DOMException && e.name === "AbortError";
    return { ...base, status: null, ok: false, latencyMs: Math.round(performance.now() - started), requestId: null,
      data: null, raw: null,
      error: aborted ? "Request cancelled." : "Could not reach the API. It may be starting up or unreachable from this network." };
  }
}

export function curlFor(method: string, path: string, body?: unknown): string {
  const parts = [`curl -X ${method} '${API_BASE || "https://YOUR-API-HOST"}${path}'`];
  if (body !== undefined) {
    parts.push(`-H 'Content-Type: application/json'`);
    parts.push(`-d '${JSON.stringify(body).replace(/'/g, "'\\''")}'`);
  }
  return parts.join(" \\\n  ");
}
