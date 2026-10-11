import { useState, type ReactNode } from "react";
import { curlFor, type Exchange, type IntegrationState } from "../api";

const STATE_LABEL: Record<IntegrationState | "checking", string> = {
  connected: "Connected",
  disconnected: "Disconnected",
  unavailable: "Not configured",
  demo: "Demo data",
  checking: "Checking",
};

export function StatusBadge({ state }: { state: IntegrationState | "checking" }) {
  return <span className={`badge ${state}`}>{STATE_LABEL[state]}</span>;
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="btn btn-sm copy"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        } catch {
          /* clipboard unavailable (e.g. insecure context); the text remains selectable */
        }
      }}
    >
      {done ? "Copied" : label}
    </button>
  );
}

export function CodeBlock({ code, copy = true, label }: { code: string; copy?: boolean; label?: string }) {
  return (
    <div className="code-wrap">
      <pre className="code" aria-label={label} tabIndex={0}>{code}</pre>
      {copy && <CopyButton text={code} />}
    </div>
  );
}

// Syntax-highlighted JSON built from React nodes (no innerHTML).
function renderJson(value: unknown, indent: number): ReactNode[] {
  const pad = "  ".repeat(indent);
  const padIn = "  ".repeat(indent + 1);
  if (value === null || typeof value === "boolean") return [<span key="l" className="json-lit">{String(value)}</span>];
  if (typeof value === "number") return [<span key="n" className="json-num">{value}</span>];
  if (typeof value === "string") return [<span key="s" className="json-str">{JSON.stringify(value)}</span>];
  if (Array.isArray(value)) {
    if (value.length === 0) return ["[]"];
    const out: ReactNode[] = ["[\n"];
    value.forEach((v, i) => {
      out.push(padIn, <span key={`a${i}`}>{renderJson(v, indent + 1)}</span>, i < value.length - 1 ? ",\n" : "\n");
    });
    out.push(pad, "]");
    return out;
  }
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) return ["{}"];
  const out: ReactNode[] = ["{\n"];
  entries.forEach(([k, v], i) => {
    out.push(
      padIn,
      <span key={`k${i}`} className="json-key">{JSON.stringify(k)}</span>,
      ": ",
      <span key={`v${i}`}>{renderJson(v, indent + 1)}</span>,
      i < entries.length - 1 ? ",\n" : "\n",
    );
  });
  out.push(pad, "}");
  return out;
}

export function JsonView({ value, label }: { value: unknown; label?: string }) {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return (
    <div className="code-wrap">
      <pre className="code" aria-label={label} tabIndex={0}>
        {typeof value === "string" ? value : renderJson(value, 0)}
      </pre>
      <CopyButton text={text} />
    </div>
  );
}

export function StatusLine<T>({ ex }: { ex: Exchange<T> }) {
  const tone = ex.status === null ? "disconnected" : ex.ok ? "connected" : ex.status === 429 ? "unavailable" : "disconnected";
  return (
    <div className="row small">
      <span className={`badge plain ${tone}`}>{ex.status === null ? "No response" : `HTTP ${ex.status}`}</span>
      <span className="mono muted">{ex.method} {ex.path}</span>
      <span className="muted">{ex.latencyMs} ms round trip</span>
      {ex.requestId && <span className="muted mono">request {ex.requestId}</span>}
    </div>
  );
}

/** Request/response inspector: parameters, status, latency, raw JSON, and a reproducible curl. */
export function ExchangeInspector<T>({ ex }: { ex: Exchange<T> }) {
  return (
    <div className="stack">
      <StatusLine ex={ex} />
      <div className="grid grid-2">
        <div>
          <h4>Request body</h4>
          {ex.requestBody === undefined ? <p className="muted small">No body (GET).</p> : <JsonView value={ex.requestBody} label="Request JSON" />}
        </div>
        <div>
          <h4>curl</h4>
          <CodeBlock code={curlFor(ex.method, ex.path, ex.requestBody)} label="curl command" />
        </div>
      </div>
      <div>
        <h4>Response body</h4>
        {ex.raw === null ? <p className="muted small">{ex.error ?? "Empty response."}</p> : <JsonView value={ex.raw} label="Response JSON" />}
      </div>
    </div>
  );
}

export function ErrorNotice({ message, hint }: { message: string; hint?: ReactNode }) {
  return (
    <div className="notice error" role="alert">
      <div>
        <strong>Request failed.</strong> {message}
        {hint && <div className="small" style={{ marginTop: 4 }}>{hint}</div>}
      </div>
    </div>
  );
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="stack" aria-hidden="true" style={{ gap: 8 }}>
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="skeleton" style={{ width: `${90 - i * 15}%` }} />
      ))}
    </div>
  );
}

export function Tabs<K extends string>({ tabs, value, onChange, label }: {
  tabs: { key: K; label: string }[];
  value: K;
  onChange: (k: K) => void;
  label: string;
}) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs.map((t) => (
        <button key={t.key} type="button" role="tab" aria-selected={value === t.key} onClick={() => onChange(t.key)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function shortHash(h: string, n = 12): string {
  return h.length > n ? `${h.slice(0, n)}…` : h;
}
