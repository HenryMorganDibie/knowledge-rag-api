"""
Demo-mode engine: a self-contained, deterministic knowledge base built from
the bundled fictional corpus in demo/corpus.

What is production code and what is a stand-in is reported in every
response (see IMPLEMENTATION):
  - Chunking:        production StructureAwareChunker
  - Fingerprinting:  production compute_fingerprint (SHA-256)
  - Fusion:          production _reciprocal_rank_fusion (k=60)
  - ACL rule:        same fail-closed rule as the live SQL (anonymous = public only)
  - Vector leg:      stand-in. Hashed word + character-trigram embedding instead
                     of text-embedding-3-small + pgvector HNSW
  - Keyword leg:     stand-in. Okapi BM25 in Python instead of PostgreSQL ts_rank
  - Reranker:        not applied (live uses a cross-encoder)
  - Answer:          extractive sentence selection, no LLM call

No network calls, no paid APIs, no visitor data stored.
"""

import json
import math
import re
import time
import uuid
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from ingestion.processors.chunker import StructureAwareChunker
from retrieval.hybrid_retriever import RetrievedChunk, _reciprocal_rank_fusion
from storage.s3_client import compute_fingerprint

CORPUS_DIR = Path(__file__).parent / "corpus"
EMBED_DIM = 1024
VECTOR_TOP_K = 20
BM25_TOP_K = 20
MIN_VECTOR_SCORE = 0.05
# A chunk is considered evidence for an answer only above these thresholds.
ANSWER_MIN_VECTOR = 0.22
ANSWER_MIN_BM25 = 1.0

IMPLEMENTATION = {
    "chunking": "production: ingestion/processors/chunker.py StructureAwareChunker",
    "fingerprint": "production: storage/s3_client.py compute_fingerprint (SHA-256)",
    "fusion": "production: retrieval/hybrid_retriever.py _reciprocal_rank_fusion (k=60)",
    "acl_filter": "fail-closed rule identical to the live SQL: anonymous callers see public chunks only",
    "vector_leg": f"demo stand-in: hashed word+trigram embedding ({EMBED_DIM}-d, cosine). "
                  "Live: OpenAI text-embedding-3-small + pgvector HNSW",
    "keyword_leg": "demo stand-in: Okapi BM25 (k1=1.2, b=0.75) in Python. Live: PostgreSQL ts_rank over a GIN tsvector index",
    "reranker": "not applied in demo mode. Live: cross-encoder/ms-marco-MiniLM-L-6-v2",
    "answer": "extractive sentence selection with citations; no LLM call. Live: grounded generation via the configured LLM",
}

_STOPWORDS = frozenset("""
a an and are as at be been but by can do does for from has have how i if in into is it its
me my no not of on or our should so than that the their them then there these they this to
use used using was we what when where which who why will with without you your
""".split())

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def _stem(word: str) -> str:
    """Tiny suffix stemmer: enough to conflate authenticate/authenticated, policy/policies."""
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    for suffix in ("ing", "ed", "s"):
        if not word.endswith(suffix) or len(word) - len(suffix) < 3:
            continue
        if (suffix == "ed" and word.endswith("eed")) or (suffix == "s" and word.endswith("ss")):
            continue  # exceed, access: not inflections
        word = word[: -len(suffix)]
        break
    if word.endswith("e") and len(word) >= 4:
        word = word[:-1]
    return word


def tokenize(text: str) -> List[str]:
    return [_stem(w) for w in _TOKEN_RE.findall(text.lower()) if w not in _STOPWORDS and len(w) > 1]


def _hash(feature: str) -> int:
    return zlib.crc32(feature.encode("utf-8"))


def embed(text: str) -> Dict[int, float]:
    """Deterministic sparse embedding: signed feature hashing of words and character trigrams."""
    counts: Dict[str, int] = {}
    for tok in tokenize(text):
        padded = f"#{tok}#"
        for feature in [f"w:{tok}"] + [f"t:{padded[i:i + 3]}" for i in range(len(padded) - 2)]:
            counts[feature] = counts.get(feature, 0) + 1
    vec: Dict[int, float] = {}
    for feature, count in counts.items():
        # Sublinear term frequency; whole words weigh more than trigrams.
        weight = (1.0 + math.log(count)) * (1.0 if feature.startswith("w:") else 0.35)
        h = _hash(feature)
        idx = h % EMBED_DIM
        sign = 1.0 if (h >> 16) & 1 else -1.0
        vec[idx] = vec.get(idx, 0.0) + sign * weight
    norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {k: v / norm for k, v in vec.items()}


def cosine(a: Dict[int, float], b: Dict[int, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(k, 0.0) for k, v in a.items())


@dataclass
class DemoChunk:
    chunk_id: str
    document_id: str
    chunk_index: int
    content: str
    section_path: str
    heading: str
    chunk_type: str
    token_count: int
    acl_groups: List[str]
    tokens: List[str] = field(default_factory=list)
    vector: Dict[int, float] = field(default_factory=dict)


@dataclass
class DemoDocument:
    id: str
    title: str
    source_type: str
    category_path: str
    acl_groups: List[str]
    updated_at: str
    html: str
    fingerprint: str
    chunk_ids: List[str]
    upstream_revision: Optional[dict] = None

    @property
    def is_public(self) -> bool:
        return not self.acl_groups


@dataclass
class DemoCollection:
    id: str
    name: str
    source_type: str
    description: str
    simulated_origin: str
    sample_questions: List[str]
    documents: Dict[str, DemoDocument]
    chunks: List[DemoChunk]


@dataclass
class DemoStore:
    notice: str
    collections: Dict[str, DemoCollection]
    built_at: str

    def document(self, document_id: str) -> Optional[DemoDocument]:
        for coll in self.collections.values():
            if document_id in coll.documents:
                return coll.documents[document_id]
        return None


def _chunk_document(doc_id: str, title: str, html: str, acl_groups: List[str], fingerprint: str) -> List[DemoChunk]:
    chunker = StructureAwareChunker()
    out = []
    for c in chunker.chunk_html(html, base_title=title):
        # Deterministic IDs: stable across restarts for the same content revision.
        cid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"demo:{doc_id}:{fingerprint}:{c.chunk_index}"))
        out.append(DemoChunk(
            chunk_id=cid, document_id=doc_id, chunk_index=c.chunk_index, content=c.content,
            section_path=c.section_path, heading=c.heading or "", chunk_type=c.chunk_type,
            token_count=c.token_count, acl_groups=list(acl_groups),
            tokens=tokenize(c.content), vector=embed(c.content),
        ))
    return out


def build_store(corpus_dir: Path = CORPUS_DIR) -> DemoStore:
    manifest = json.loads((corpus_dir / "manifest.json").read_text(encoding="utf-8"))
    collections: Dict[str, DemoCollection] = {}
    for c in manifest["collections"]:
        docs: Dict[str, DemoDocument] = {}
        chunks: List[DemoChunk] = []
        for d in c["documents"]:
            html = (corpus_dir / c["id"] / d["file"]).read_text(encoding="utf-8")
            fp = compute_fingerprint(html.encode("utf-8"))
            doc_chunks = _chunk_document(d["id"], d["title"], html, d["acl_groups"], fp)
            rev = d.get("upstream_revision")
            if rev:
                rev = {"html": (corpus_dir / c["id"] / rev["file"]).read_text(encoding="utf-8"),
                       "change_summary": rev["change_summary"]}
            docs[d["id"]] = DemoDocument(
                id=d["id"], title=d["title"], source_type=c["source_type"], category_path=d["category_path"],
                acl_groups=list(d["acl_groups"]), updated_at=d["updated_at"], html=html, fingerprint=fp,
                chunk_ids=[x.chunk_id for x in doc_chunks], upstream_revision=rev,
            )
            chunks.extend(doc_chunks)
        collections[c["id"]] = DemoCollection(
            id=c["id"], name=c["name"], source_type=c["source_type"], description=c["description"],
            simulated_origin=c["simulated_origin"], sample_questions=c["sample_questions"],
            documents=docs, chunks=chunks,
        )
    return DemoStore(notice=manifest["notice"], collections=collections,
                     built_at=datetime.now(timezone.utc).isoformat())


@lru_cache(maxsize=1)
def get_demo_store() -> DemoStore:
    return build_store()


# --------------------------------------------------------------------------- retrieval

def _visible_chunks(coll: DemoCollection) -> List[DemoChunk]:
    # Demo callers are always anonymous: public chunks only (fail closed).
    return [c for c in coll.chunks if not c.acl_groups]


def _bm25_scores(query_tokens: List[str], chunks: List[DemoChunk], k1: float = 1.2, b: float = 0.75) -> Dict[str, float]:
    n = len(chunks)
    if not n or not query_tokens:
        return {}
    avgdl = sum(len(c.tokens) for c in chunks) / n or 1.0
    df: Dict[str, int] = {}
    for c in chunks:
        for t in set(c.tokens):
            df[t] = df.get(t, 0) + 1
    scores: Dict[str, float] = {}
    for c in chunks:
        tf: Dict[str, int] = {}
        for t in c.tokens:
            tf[t] = tf.get(t, 0) + 1
        s = 0.0
        for q in set(query_tokens):
            if q not in tf:
                continue
            idf = math.log(1 + (n - df[q] + 0.5) / (df[q] + 0.5))
            s += idf * tf[q] * (k1 + 1) / (tf[q] + k1 * (1 - b + b * len(c.tokens) / avgdl))
        if s > 0:
            scores[c.chunk_id] = s
    return scores


def _to_retrieved(c: DemoChunk, **scores) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=c.chunk_id, source_id=c.document_id, content=c.content, section_path=c.section_path,
        heading=c.heading, chunk_type=c.chunk_type, acl_groups=c.acl_groups, **scores,
    )


def retrieve(collection_id: str, query: str, top_k: int = 5, mode: str = "hybrid") -> dict:
    store = get_demo_store()
    coll = store.collections[collection_id]
    started = time.perf_counter()

    visible = _visible_chunks(coll)
    by_id = {c.chunk_id: c for c in visible}
    q_tokens = tokenize(query)
    q_vec = embed(query)

    vec_scored = sorted(((cosine(q_vec, c.vector), c) for c in visible), key=lambda x: x[0], reverse=True)
    vector_hits = [_to_retrieved(c, vector_score=round(s, 4)) for s, c in vec_scored if s >= MIN_VECTOR_SCORE][:VECTOR_TOP_K]

    bm25 = _bm25_scores(q_tokens, visible)
    bm25_ranked = sorted(bm25.items(), key=lambda x: x[1], reverse=True)[:BM25_TOP_K]
    bm25_hits = [_to_retrieved(by_id[cid]) for cid, _ in bm25_ranked]

    vector_rank = {c.chunk_id: i for i, c in enumerate(vector_hits, 1)}
    vector_score = {c.chunk_id: c.vector_score for c in vector_hits}
    bm25_rank = {cid: i for i, (cid, _) in enumerate(bm25_ranked, 1)}

    if mode == "vector":
        ordered = vector_hits
    elif mode == "keyword":
        ordered = bm25_hits
    else:
        ordered = _reciprocal_rank_fusion(vector_hits, bm25_hits)

    final = ordered[:top_k]
    chunks_out = []
    for r in final:
        c = by_id[r.chunk_id]
        doc = coll.documents[c.document_id]
        chunks_out.append({
            "chunk_id": c.chunk_id,
            "source_id": c.document_id,
            "content": c.content,
            "section_path": c.section_path,
            "heading": c.heading,
            "chunk_type": c.chunk_type,
            "vector_score": vector_score.get(c.chunk_id),
            "rrf_score": round(r.rrf_score, 4) if mode == "hybrid" else None,
            "rerank_score": None,
            # Demo-only enrichment (the live SQL returns source_id only):
            "document_title": doc.title,
            "category_path": doc.category_path,
            "source_type": doc.source_type,
            "vector_rank": vector_rank.get(c.chunk_id),
            "bm25_rank": bm25_rank.get(c.chunk_id),
            "bm25_score": round(bm25[c.chunk_id], 4) if c.chunk_id in bm25 else None,
        })

    return {
        "mode": "demo",
        "retrieval_mode": mode,
        "collection_id": collection_id,
        "query": query,
        "chunks": chunks_out,
        "total_retrieved": len(ordered),
        "diagnostics": {
            "vector_hits": len(vector_hits),
            "bm25_hits": len(bm25_hits),
            "after_rrf": len(ordered) if mode == "hybrid" else None,
            "after_rerank": None,
            "rerank_applied": False,
            "acl_filter_applied": True,
            "acl_public_only": True,
            "acl_excluded_chunks": len(coll.chunks) - len(visible),
            "chunks_searched": len(visible),
            "query_terms": sorted(set(q_tokens)),
            "top_rrf_scores": [
                {"chunk_id": r.chunk_id, "rrf_score": round(r.rrf_score, 4)} for r in ordered[:10]
            ] if mode == "hybrid" else [],
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "implementation": IMPLEMENTATION,
        },
    }


# --------------------------------------------------------------------------- answers

def _units(chunk: dict) -> List[str]:
    """Answerable units: sentences for prose, readable rows for tables."""
    content = chunk["content"]
    if chunk["chunk_type"] == "table":
        rows = [r.strip().strip("|").split("|") for r in content.splitlines() if r.strip()]
        rows = [[cell.strip() for cell in r] for r in rows if not all(set(cell.strip()) <= {"-"} for cell in r)]
        if len(rows) < 2:
            return []
        header, body = rows[0], rows[1:]
        return ["; ".join(f"{h}: {v}" for h, v in zip(header, r)) + "." for r in body]
    heading = chunk.get("heading") or ""
    if heading and content.startswith(heading):
        content = content[len(heading):].strip()
    return [s.strip() for s in _SENTENCE_RE.split(content) if len(s.strip()) > 20]


def answer(collection_id: str, query: str, top_k: int = 5, mode: str = "hybrid") -> dict:
    started = time.perf_counter()
    retrieval = retrieve(collection_id, query, top_k=top_k, mode=mode)
    chunks = retrieval["chunks"]

    evidence = [
        c for c in chunks
        if (c["bm25_score"] or 0) >= ANSWER_MIN_BM25 or (c["vector_score"] or 0) >= ANSWER_MIN_VECTOR
    ][:3]

    # Rank candidate sentences/rows across all evidence chunks by IDF-weighted
    # query-term overlap, so specific matches beat generic introductions.
    store = get_demo_store()
    visible = _visible_chunks(store.collections[collection_id])
    df: Dict[str, int] = {}
    for c in visible:
        for t in set(c.tokens):
            df[t] = df.get(t, 0) + 1
    n = len(visible)
    q_terms = set(tokenize(query))
    candidates = []  # (score, chunk_rank, chunk, unit)
    idf = lambda terms: sum(math.log(1 + n / df.get(t, 1)) for t in terms)  # noqa: E731
    for rank, chunk in enumerate(evidence):
        # A matching section heading is strong evidence that its sentences answer the question.
        # The document's own title (top-level section) is excluded: it matches everything in the doc.
        is_subsection = ">" in (chunk.get("section_path") or "")
        heading_bonus = 0.5 * idf(q_terms & set(tokenize(chunk.get("heading") or ""))) if is_subsection else 0.0
        units = _units(chunk)
        for pos, unit in enumerate(units):
            matched = q_terms & set(tokenize(unit))
            if matched:
                candidates.append((idf(matched) + heading_bonus, rank, chunk, unit, units[pos + 1:pos + 2]))
    candidates.sort(key=lambda x: (-x[0], x[1]))

    picked: List[tuple] = []  # (citation_index, sentence)
    citations = []
    cited: Dict[str, int] = {}
    if candidates:
        best = candidates[0][0]
        for score, _, chunk, unit, _following in candidates:
            if len(picked) == 3 or score < 0.75 * best:
                break
            if chunk["chunk_id"] not in cited:
                cited[chunk["chunk_id"]] = len(citations) + 1
                citations.append({
                    "index": cited[chunk["chunk_id"]],
                    "chunk_id": chunk["chunk_id"],
                    "document_id": chunk["source_id"],
                    "document_title": chunk["document_title"],
                    "source_type": chunk["source_type"],
                    "section_path": chunk["section_path"],
                    "heading": chunk["heading"],
                    "excerpt": chunk["content"][:200] + "..." if len(chunk["content"]) > 200 else chunk["content"],
                })
            picked.append((cited[chunk["chunk_id"]], unit))
        # A lone prose sentence usually needs the one after it for context.
        top_chunk, following = candidates[0][2], candidates[0][4]
        if len(picked) == 1 and top_chunk["chunk_type"] == "text" and following:
            picked.append((picked[0][0], following[0]))

    if picked:
        answer_text = " ".join(f"{u} [{i}]" for i, u in picked)
        grounded = True
    else:
        answer_text = ("The sample knowledge base does not contain information that answers this question. "
                       "Try one of the suggested questions for this collection.")
        grounded = False

    return {
        "mode": "demo",
        "answer": answer_text,
        "grounded": grounded,
        "citations": citations,
        "chunks_used": len(citations),
        "model": "extractive-demo (no LLM)",
        "retrieval": retrieval,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
    }


# --------------------------------------------------------------------------- ingestion

RETRY_GUIDANCE = {
    "document360": [
        "Confirm DOCUMENT360_API_KEY is a current API token (Document360 > Settings > API tokens).",
        "Confirm DOCUMENT360_PROJECT_ID is the project version ID the token can read.",
        "Re-run the sync: unchanged articles are skipped by fingerprint, so retries are cheap and idempotent.",
    ],
    "sharepoint": [
        "Check that AZURE_CLIENT_SECRET has not expired in the Entra ID app registration.",
        "Confirm the app has admin-consented Microsoft Graph application permission Sites.Read.All (or Sites.Selected with a site grant).",
        "Re-run the sync: unchanged files are skipped by fingerprint, so retries are cheap and idempotent.",
    ],
}

SIMULATED_AUTH_ERRORS = {
    "document360": "Document360 API returned HTTP 401 Unauthorized for GET /v2/articles (simulated).",
    "sharepoint": "Token request to login.microsoftonline.com failed: invalid_client, client secret expired (simulated).",
}


def run_sync(collection_id: str, scenario: str) -> dict:
    """
    Execute the ingestion decision logic against the bundled sources in an
    isolated, request-local index. The shared published demo index is never
    modified, so concurrent visitors always see the same data.
    """
    store = get_demo_store()
    coll = store.collections[collection_id]
    started_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    job_id = str(uuid.uuid4())

    base = {
        "job_id": job_id,
        "mode": "demo",
        "collection_id": collection_id,
        "source_type": coll.source_type,
        "scenario": scenario,
        "started_at": started_at.isoformat(),
        "sandboxed": True,
        "note": "Runs the production chunker and SHA-256 fingerprinting over bundled sample files in an isolated "
                "index. Connector HTTP calls are not executed and the shared demo index is not modified.",
    }

    if scenario == "auth_failure":
        return {
            **base,
            "status": "failed",
            "documents_scanned": 0, "documents_changed": 0, "documents_skipped": 0, "chunks_created": 0,
            "error_detail": SIMULATED_AUTH_ERRORS[coll.source_type],
            "retry_guidance": RETRY_GUIDANCE[coll.source_type],
            "documents": [],
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    results = []
    changed = skipped = created = 0
    for doc in coll.documents.values():
        html, change_summary = doc.html, None
        if scenario == "upstream_edit" and doc.upstream_revision:
            html, change_summary = doc.upstream_revision["html"], doc.upstream_revision["change_summary"]
        fp = compute_fingerprint(html.encode("utf-8"))
        entry = {
            "document_id": doc.id,
            "title": doc.title,
            "acl_groups": doc.acl_groups,
            "published_fingerprint": doc.fingerprint,
            "incoming_fingerprint": fp,
        }
        if fp == doc.fingerprint:
            skipped += 1
            results.append({**entry, "action": "skipped", "reason": "fingerprint unchanged",
                            "revision": 1, "chunks_deleted": 0, "chunks_inserted": 0})
            continue
        new_chunks = _chunk_document(doc.id, doc.title, html, doc.acl_groups, fp)
        changed += 1
        created += len(new_chunks)
        results.append({
            **entry,
            "action": "republished",
            "reason": change_summary,
            "revision": 2,
            "chunks_deleted": len(doc.chunk_ids),
            "chunks_inserted": len(new_chunks),
            "new_chunks": [
                {"chunk_index": c.chunk_index, "chunk_type": c.chunk_type, "section_path": c.section_path,
                 "token_count": c.token_count}
                for c in new_chunks
            ],
            "atomic_publish": [
                "BEGIN",
                f"DELETE FROM document_chunks WHERE source_id = '{doc.id}'  -- {len(doc.chunk_ids)} rows",
                f"INSERT INTO document_chunks ...  -- {len(new_chunks)} rows, revision 2",
                "UPDATE document_sources SET content_fingerprint = :new_fingerprint",
                "COMMIT  -- readers see either all old chunks or all new chunks, never a mix",
            ],
        })

    return {
        **base,
        "status": "completed",
        "documents_scanned": len(results),
        "documents_changed": changed,
        "documents_skipped": skipped,
        "chunks_created": created,
        "error_detail": None,
        "retry_guidance": [],
        "documents": results,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
    }
