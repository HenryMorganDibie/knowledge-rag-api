import { Fragment, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import {
  call,
  type AnswerResponse,
  type CollectionId,
  type CollectionsResponse,
  type Exchange,
  type RetrievalMode,
  type RetrievalResponse,
} from "../api";
import { ErrorNotice, ExchangeInspector, Skeleton, StatusLine, Tabs } from "./common";
import { SourceDrawer } from "./SourceDrawer";

const MAX_QUERY = 500;
const MODES: { key: RetrievalMode; label: string; hint: string }[] = [
  { key: "hybrid", label: "Hybrid (RRF)", hint: "Vector and keyword lists merged with Reciprocal Rank Fusion" },
  { key: "vector", label: "Vector only", hint: "Embedding similarity only" },
  { key: "keyword", label: "Keyword only", hint: "BM25 full-text only" },
];

type ResultTab = "answer" | "passages" | "trace" | "compare" | "http";

function AnswerText({ text, onCite }: { text: string; onCite: (n: number) => void }) {
  const parts: ReactNode[] = [];
  const re = /\[(\d+)\]/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    parts.push(text.slice(last, m.index));
    const n = Number(m[1]);
    parts.push(
      <button key={m.index} type="button" className="cite-ref" onClick={() => onCite(n)} aria-label={`Open source ${n}`}>
        {n}
      </button>,
    );
    last = m.index + m[0].length;
  }
  parts.push(text.slice(last));
  return <p className="answer">{parts}</p>;
}

function Score({ label, value, max }: { label: string; value: number | null; max: number }) {
  if (value == null) return <span className="muted">{label}: n/a</span>;
  return (
    <span style={{ minWidth: 110, display: "inline-block" }}>
      {label}: <span className="mono">{value.toFixed(3)}</span>
      <span className="scorebar"><span style={{ width: `${Math.min(100, (value / max) * 100)}%` }} /></span>
    </span>
  );
}

function Passages({ r, onOpen }: { r: RetrievalResponse; onOpen: (doc: string, chunk: string) => void }) {
  if (!r.chunks.length) return <div className="empty">No passages matched this query.</div>;
  const maxVec = Math.max(...r.chunks.map((c) => c.vector_score ?? 0), 0.01);
  const maxBm = Math.max(...r.chunks.map((c) => c.bm25_score ?? 0), 0.01);
  const maxRrf = Math.max(...r.chunks.map((c) => c.rrf_score ?? 0), 0.0001);
  return (
    <div>
      {r.chunks.map((c, i) => (
        <article className="passage" key={c.chunk_id}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <div>
              <strong>#{i + 1} {c.heading}</strong>{" "}
              <span className="tag">{c.chunk_type}</span>
              <div className="muted small">{c.document_title} · {c.section_path}</div>
            </div>
            <button type="button" className="btn btn-sm" onClick={() => onOpen(c.source_id, c.chunk_id)}>Open source</button>
          </div>
          <div className="row small" style={{ marginTop: 8, gap: 18 }}>
            <Score label="vector" value={c.vector_score} max={maxVec} />
            <Score label="bm25" value={c.bm25_score} max={maxBm} />
            {r.retrieval_mode === "hybrid" && <Score label="rrf" value={c.rrf_score} max={maxRrf} />}
            <span className="muted">ranks: vector {c.vector_rank ?? "–"} · keyword {c.bm25_rank ?? "–"}</span>
          </div>
          <div className="content">{c.content}</div>
        </article>
      ))}
    </div>
  );
}

function Trace({ r }: { r: RetrievalResponse }) {
  const d = r.diagnostics;
  const rows: [string, ReactNode][] = [
    ["retrieval_mode", r.retrieval_mode],
    ["query_terms", d.query_terms.join(", ") || "(none)"],
    ["chunks_searched", d.chunks_searched],
    ["acl_excluded_chunks", `${d.acl_excluded_chunks} (restricted; anonymous caller is public-only)`],
    ["vector_hits", d.vector_hits],
    ["bm25_hits", d.bm25_hits],
    ["after_rrf", d.after_rrf ?? "n/a"],
    ["rerank_applied", String(d.rerank_applied)],
    ["server_latency_ms", d.latency_ms],
  ];
  return (
    <div className="grid grid-2">
      <div>
        <h4>Pipeline counters</h4>
        <dl className="kv">{rows.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}</dl>
        {d.top_rrf_scores.length > 0 && (
          <>
            <h4 style={{ marginTop: 14 }}>Top RRF scores</h4>
            <table className="data">
              <tbody>
                {d.top_rrf_scores.slice(0, 6).map((s) => (
                  <tr key={s.chunk_id}><td className="mono small">{s.chunk_id.slice(0, 8)}</td><td className="num">{s.rrf_score.toFixed(4)}</td></tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
      <div>
        <h4>Implementation of each stage</h4>
        <dl className="kv" style={{ gridTemplateColumns: "max-content 1fr" }}>
          {Object.entries(d.implementation).map(([k, v]) => (
            <Fragment key={k}><dt>{k}</dt><dd style={{ fontFamily: "var(--sans)" }}>{v}</dd></Fragment>
          ))}
        </dl>
      </div>
    </div>
  );
}

function Compare({ results, loading, onRun }: {
  results: Exchange<RetrievalResponse>[] | null;
  loading: boolean;
  onRun: () => void;
}) {
  if (!results && !loading) {
    return (
      <div className="empty">
        <p>Run the same query through each retrieval strategy and compare the rankings.</p>
        <button type="button" className="btn btn-primary" onClick={onRun}>Compare retrieval modes</button>
      </div>
    );
  }
  if (loading) return <Skeleton lines={5} />;
  return (
    <>
      <p className="muted small">
        Hybrid ranks a passage highly when both strategies agree. Vector similarity rewards related wording; keyword search rewards exact terms.
      </p>
      <div className="compare">
        {results!.map((ex, i) => (
          <div className="card" key={MODES[i].key} style={{ marginTop: 0 }}>
            <h3>{MODES[i].label}</h3>
            <p className="muted small">{MODES[i].hint}</p>
            {ex.ok && ex.data ? (
              ex.data.chunks.length ? (
                <ol>
                  {ex.data.chunks.map((c) => (
                    <li key={c.chunk_id}>
                      <strong>{c.heading}</strong>
                      <div className="muted small">{c.document_title}</div>
                    </li>
                  ))}
                </ol>
              ) : <div className="empty">No results</div>
            ) : <ErrorNotice message={ex.error ?? "Failed"} />}
          </div>
        ))}
      </div>
    </>
  );
}

export function Playground({ collections }: { collections: Exchange<CollectionsResponse> | null }) {
  const list = collections?.data?.collections ?? [];
  const [collectionId, setCollectionId] = useState<CollectionId>("lumen-developer-docs");
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<RetrievalMode>("hybrid");
  const [topK, setTopK] = useState(5);
  const [validation, setValidation] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [ex, setEx] = useState<Exchange<AnswerResponse> | null>(null);
  const [tab, setTab] = useState<ResultTab>("answer");
  const [compare, setCompare] = useState<Exchange<RetrievalResponse>[] | null>(null);
  const [comparing, setComparing] = useState(false);
  const [drawer, setDrawer] = useState<{ doc: string; chunk?: string } | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);
  const collection = list.find((c) => c.id === collectionId);

  async function ask(q: string) {
    const trimmed = q.trim();
    if (!trimmed) return setValidation("Enter a question.");
    if (trimmed.length > MAX_QUERY) return setValidation(`Questions are limited to ${MAX_QUERY} characters.`);
    setValidation(null);
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setLoading(true);
    setCompare(null);
    const res = await call<AnswerResponse>("POST", "/demo/ask",
      { collection_id: collectionId, query: trimmed, top_k: topK, retrieval_mode: mode }, ctrl.signal);
    if (ctrl.signal.aborted) return;
    setEx(res);
    setLoading(false);
    if (!res.ok) setTab("http");
    else if (tab === "compare" || tab === "http") setTab("answer");
  }

  async function runCompare() {
    const q = (ex?.data?.retrieval.query ?? query).trim();
    if (!q) return setValidation("Enter a question first.");
    setComparing(true);
    const results = await Promise.all(MODES.map((m) =>
      call<RetrievalResponse>("POST", "/demo/retrieve", { collection_id: collectionId, query: q, top_k: 5, retrieval_mode: m.key })));
    setCompare(results);
    setComparing(false);
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    void ask(query);
  }

  const data = ex?.data;
  const openCitation = (n: number) => {
    const c = data?.citations.find((x) => x.index === n);
    if (c) setDrawer({ doc: c.document_id, chunk: c.chunk_id });
  };

  return (
    <>
      <div className="page-head">
        <h1>API playground</h1>
        <p>
          Ask a question against a sample collection. The request goes to <code>POST /demo/ask</code>, which retrieves passages,
          fuses rankings, enforces ACLs, and returns an extractive answer with citations. Every number shown is computed by the backend.
        </p>
      </div>

      <form className="card" onSubmit={submit} noValidate>
        <div className="grid grid-3">
          <label className="field">
            Collection
            <select value={collectionId} onChange={(e) => { setCollectionId(e.target.value as CollectionId); setEx(null); setCompare(null); }}>
              {(list.length ? list : [{ id: "lumen-developer-docs", name: "Lumen Cloud Developer Docs" }, { id: "lumen-it-operations", name: "Lumen IT Operations Library" }])
                .map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          <div className="field" role="group" aria-label="Retrieval mode">
            <span style={{ fontSize: "0.82rem", fontWeight: 600, color: "var(--text-2)" }}>Retrieval mode</span>
            <div className="segmented">
              {MODES.map((m) => (
                <button key={m.key} type="button" aria-pressed={mode === m.key} title={m.hint} onClick={() => setMode(m.key)}>{m.label.split(" ")[0]}</button>
              ))}
            </div>
          </div>
          <label className="field">
            top_k: {topK}
            <input type="range" min={1} max={10} value={topK} onChange={(e) => setTopK(Number(e.target.value))} />
          </label>
        </div>
        {collection && (
          <p className="muted small" style={{ margin: "10px 0 0" }}>
            {collection.description} Simulated origin: {collection.simulated_origin}. {collection.documents.length} public documents,{" "}
            {collection.chunk_count} chunks{collection.restricted_documents_hidden ? `, ${collection.restricted_documents_hidden} restricted document hidden from anonymous callers` : ""}.
          </p>
        )}
        <label className="field" style={{ marginTop: 12 }}>
          Question
          <textarea
            value={query}
            maxLength={MAX_QUERY + 50}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void ask(query); }}
            placeholder="e.g. What happens when I exceed the rate limit?"
            aria-invalid={!!validation}
            aria-describedby="q-help"
          />
        </label>
        <div id="q-help" className="row small" style={{ justifyContent: "space-between", marginTop: 4 }}>
          {validation ? <span className="field-error" role="alert">{validation}</span> : <span className="muted">Ctrl/Cmd + Enter to submit</span>}
          <span className={query.length > MAX_QUERY ? "field-error" : "muted"}>{query.length}/{MAX_QUERY}</span>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          {collection?.sample_questions.map((q) => (
            <button key={q} type="button" className="chip" onClick={() => { setQuery(q); void ask(q); }}>{q}</button>
          ))}
        </div>
        <div className="row" style={{ marginTop: 14 }}>
          <button type="submit" className="btn btn-primary" disabled={loading}>{loading ? "Running…" : "Ask"}</button>
          {loading && <span className="muted small" role="status">Retrieving and ranking passages…</span>}
        </div>
      </form>

      <section className="card" aria-live="polite" aria-busy={loading}>
        {!ex && !loading && <div className="empty">Results appear here. Try one of the suggested questions above.</div>}
        {loading && !ex && <Skeleton lines={4} />}
        {ex && (
          <>
            <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
              <StatusLine ex={ex} />
              {data && <span className="tag demo">mode: demo · {data.model}</span>}
            </div>
            <Tabs<ResultTab>
              label="Result views"
              value={tab}
              onChange={setTab}
              tabs={[
                { key: "answer", label: "Answer" },
                { key: "passages", label: `Passages${data ? ` (${data.retrieval.chunks.length})` : ""}` },
                { key: "trace", label: "Retrieval trace" },
                { key: "compare", label: "Compare modes" },
                { key: "http", label: "Request & response" },
              ]}
            />
            {!ex.ok && tab !== "http" && <ErrorNotice message={ex.error ?? "Unknown error"} />}
            {data && tab === "answer" && (
              <div>
                {data.grounded ? <AnswerText text={data.answer} onCite={openCitation} /> : (
                  <div className="notice warn"><div><strong>No grounded answer.</strong> {data.answer}</div></div>
                )}
                {data.citations.length > 0 && (
                  <>
                    <h4 style={{ marginTop: 16 }}>Sources</h4>
                    {data.citations.map((c) => (
                      <div className="citation" key={c.index}>
                        <button type="button" className="cite-ref" onClick={() => openCitation(c.index)} aria-label={`Open source ${c.index}`}>{c.index}</button>
                        <div style={{ flex: 1 }}>
                          <div><strong>{c.document_title}</strong> <span className="tag">{c.source_type}</span></div>
                          <div className="muted small">{c.section_path}</div>
                          <div className="excerpt">{c.excerpt}</div>
                        </div>
                      </div>
                    ))}
                  </>
                )}
              </div>
            )}
            {data && tab === "passages" && <Passages r={data.retrieval} onOpen={(doc, chunk) => setDrawer({ doc, chunk })} />}
            {data && tab === "trace" && <Trace r={data.retrieval} />}
            {tab === "compare" && <Compare results={compare} loading={comparing} onRun={runCompare} />}
            {tab === "http" && <ExchangeInspector ex={ex} />}
          </>
        )}
      </section>

      {drawer && <SourceDrawer documentId={drawer.doc} highlightChunk={drawer.chunk} onClose={() => setDrawer(null)} />}
    </>
  );
}
