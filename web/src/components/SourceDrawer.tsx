import { useEffect, useRef, useState } from "react";
import { call, type DocumentResponse, type Exchange } from "../api";
import { ErrorNotice, Skeleton, shortHash } from "./common";

export function SourceDrawer({ documentId, highlightChunk, onClose }: {
  documentId: string;
  highlightChunk?: string;
  onClose: () => void;
}) {
  const [ex, setEx] = useState<Exchange<DocumentResponse> | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const hlRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let live = true;
    setEx(null);
    call<DocumentResponse>("GET", `/demo/documents/${encodeURIComponent(documentId)}`).then((r) => live && setEx(r));
    return () => { live = false; };
  }, [documentId]);

  // Latest onClose without re-running the focus effect when the parent re-renders.
  const onCloseRef = useRef(onClose);
  useEffect(() => { onCloseRef.current = onClose; }, [onClose]);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCloseRef.current();
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, []);

  useEffect(() => {
    hlRef.current?.scrollIntoView({ block: "center" });
  }, [ex]);

  const doc = ex?.data;
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">
        <header>
          <div>
            <h2 id="drawer-title">{doc?.title ?? "Source document"}</h2>
            {doc && (
              <div className="muted small">
                <span className="tag">{doc.source_type}</span> {doc.category_path} · updated {doc.updated_at}
              </div>
            )}
          </div>
          <button ref={closeRef} type="button" className="btn btn-sm" onClick={onClose}>Close</button>
        </header>
        <div className="body">
          {!ex && <Skeleton lines={6} />}
          {ex && !ex.ok && <ErrorNotice message={ex.error ?? "Could not load document."} />}
          {doc && (
            <>
              <dl className="kv" style={{ marginBottom: 14 }}>
                <dt>document_id</dt><dd>{doc.id}</dd>
                <dt>fingerprint</dt><dd title={doc.fingerprint}>sha256:{shortHash(doc.fingerprint, 24)}</dd>
                <dt>acl_groups</dt><dd>{doc.acl_groups.length ? doc.acl_groups.join(", ") : "[] (public)"}</dd>
                <dt>chunks</dt><dd>{doc.chunks.length}</dd>
              </dl>
              <p className="muted small">
                Shown as stored: one block per chunk produced by the structure-aware chunker. The cited chunk is highlighted.
              </p>
              {doc.chunks.map((c) => {
                const hl = c.chunk_id === highlightChunk;
                return (
                  <div key={c.chunk_id} ref={hl ? hlRef : undefined} className={`doc-chunk${hl ? " hl" : ""}`}>
                    <div className="muted small">
                      chunk {c.chunk_index} · {c.chunk_type} · ~{c.token_count} tokens · {c.section_path}
                    </div>
                    <div className="content">{c.content}</div>
                  </div>
                );
              })}
            </>
          )}
        </div>
      </aside>
    </>
  );
}
