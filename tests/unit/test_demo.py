"""Demo mode: grounded answers, citations, retrieval modes, ACL, and sandboxed ingestion."""

import json

import pytest

from demo import engine
from demo.engine import CORPUS_DIR, get_demo_store, tokenize
from ingestion.processors.chunker import StructureAwareChunker
from storage.s3_client import compute_fingerprint

DEV = "lumen-developer-docs"
IT = "lumen-it-operations"


def test_collection_ids_match_manifest():
    from api.routes.demo import CollectionId

    manifest = json.loads((CORPUS_DIR / "manifest.json").read_text())
    assert set(CollectionId.__args__) == {c["id"] for c in manifest["collections"]}


def test_index_uses_production_chunker_and_fingerprints():
    store = get_demo_store()
    doc = store.collections[DEV].documents["d360-rate-limits"]
    assert doc.fingerprint == compute_fingerprint(doc.html.encode("utf-8"))
    expected = StructureAwareChunker().chunk_html(doc.html, base_title=doc.title)
    actual = [c for c in store.collections[DEV].chunks if c.document_id == doc.id]
    assert [c.content for c in actual] == [c.content for c in expected]


def test_chunk_ids_are_deterministic():
    a = engine.build_store()
    b = engine.build_store()
    assert [c.chunk_id for c in a.collections[DEV].chunks] == [c.chunk_id for c in b.collections[DEV].chunks]


def test_stemmer_conflates_inflections():
    assert tokenize("exceed exceeds exceeding") == ["exceed"] * 3
    assert tokenize("authenticate authenticated") == ["authenticat"] * 2
    assert tokenize("access accessed") == ["access"] * 2


@pytest.mark.parametrize("collection,question,expected_heading,expected_fact", [
    (DEV, "What happens when I exceed the rate limit?", "Exceeding the limit", "429"),
    (DEV, "Why does SAML login fail with a clock skew error?", "Troubleshooting", "3 minutes"),
    (DEV, "how many times are webhooks retried", "Retries and delivery guarantees", "5 times"),
    (IT, "What is the response time for a SEV1 incident?", "Severity levels", "15 minutes"),
    (IT, "What must I do if my laptop is lost?", "Lost or stolen devices", "24 hours"),
])
def test_answers_are_grounded_and_cited(collection, question, expected_heading, expected_fact):
    result = engine.answer(collection, question)
    assert result["grounded"] is True
    assert expected_fact in result["answer"]
    assert expected_heading in [c["heading"] for c in result["citations"]]
    # Every [n] marker in the answer refers to a returned citation.
    indices = {c["index"] for c in result["citations"]}
    for n in indices:
        assert f"[{n}]" in result["answer"]
    # Every citation points at a chunk that was actually retrieved.
    retrieved = {c["chunk_id"] for c in result["retrieval"]["chunks"]}
    assert {c["chunk_id"] for c in result["citations"]} <= retrieved


def test_unanswerable_question_is_not_fabricated():
    result = engine.answer(DEV, "What is the capital of France?")
    assert result["grounded"] is False
    assert result["citations"] == []


def test_restricted_document_never_retrieved_anonymously():
    store = get_demo_store()
    restricted = {c.chunk_id for c in store.collections[IT].chunks if c.acl_groups}
    assert restricted, "fixture must contain a restricted document"
    for mode in ("hybrid", "vector", "keyword"):
        r = engine.retrieve(IT, "break-glass admin access procedure credential vault", top_k=10, mode=mode)
        assert not restricted & {c["chunk_id"] for c in r["chunks"]}
        assert r["diagnostics"]["acl_excluded_chunks"] == len(restricted)
    answer = engine.answer(IT, "What is the break-glass admin procedure?")
    assert "vault" not in answer["answer"].lower()


def test_retrieval_modes_report_their_own_scores():
    q = "verify webhook signature"
    hybrid = engine.retrieve(DEV, q, mode="hybrid")
    vector = engine.retrieve(DEV, q, mode="vector")
    keyword = engine.retrieve(DEV, q, mode="keyword")
    assert all(c["rrf_score"] is not None for c in hybrid["chunks"])
    assert all(c["rrf_score"] is None for c in vector["chunks"] + keyword["chunks"])
    assert [c["vector_rank"] for c in vector["chunks"]] == list(range(1, len(vector["chunks"]) + 1))
    assert [c["bm25_rank"] for c in keyword["chunks"]] == list(range(1, len(keyword["chunks"]) + 1))
    assert hybrid["diagnostics"]["rerank_applied"] is False
    assert "stand-in" in hybrid["diagnostics"]["implementation"]["vector_leg"]


def test_sync_unchanged_skips_everything():
    job = engine.run_sync(DEV, "unchanged")
    assert job["status"] == "completed"
    assert job["documents_changed"] == 0
    assert job["documents_skipped"] == job["documents_scanned"] == 5
    assert all(d["action"] == "skipped" for d in job["documents"])


def test_sync_upstream_edit_republishes_only_changed_document():
    job = engine.run_sync(DEV, "upstream_edit")
    changed = [d for d in job["documents"] if d["action"] == "republished"]
    assert [d["document_id"] for d in changed] == ["d360-rate-limits"]
    d = changed[0]
    assert d["incoming_fingerprint"] != d["published_fingerprint"]
    assert d["chunks_inserted"] == job["chunks_created"] > 0
    assert any("Concurrency limits" in c["section_path"] for c in d["new_chunks"])
    # The shared published index is untouched by a sandboxed run.
    assert engine.answer(DEV, "What is the enterprise rate limit?")["answer"].count("2,000") == 0


def test_sync_auth_failure_reports_guidance():
    job = engine.run_sync(IT, "auth_failure")
    assert job["status"] == "failed"
    assert "simulated" in job["error_detail"]
    assert job["retry_guidance"]
