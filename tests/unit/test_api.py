"""HTTP-level tests: contracts, validation, auth boundaries, ACL, limits, and secret hygiene."""

import pytest

from core.config import settings
from tests.conftest import SERVICE_KEY

DEV = "lumen-developer-docs"
SECRET_VALUES = {
    "OPENAI_API_KEY": "sk-test-SECRET-openai-value",
    "DOCUMENT360_API_KEY": "d360-SECRET-token-value",
    "AZURE_CLIENT_SECRET": "azure-SECRET-client-value",
    "AWS_SECRET_ACCESS_KEY": "aws-SECRET-access-value",
    "DATABASE_URL": "postgresql+asyncpg://user:SECRET-db-password@127.0.0.1:1/x",
}


def test_demo_ask_flow_and_open_citation(client):
    r = client.post("/demo/ask", json={"collection_id": DEV, "query": "How do I verify a webhook signature?"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "demo" and body["grounded"]
    cite = body["citations"][0]
    doc = client.get(f"/demo/documents/{cite['document_id']}")
    assert doc.status_code == 200
    assert cite["chunk_id"] in {c["chunk_id"] for c in doc.json()["chunks"]}


def test_demo_collections_hide_restricted_documents(client):
    body = client.get("/demo/collections").json()
    it = next(c for c in body["collections"] if c["id"] == "lumen-it-operations")
    assert it["restricted_documents_hidden"] == 1
    assert "sp-break-glass" not in {d["id"] for d in it["documents"]}
    assert client.get("/demo/documents/sp-break-glass").status_code == 404


@pytest.mark.parametrize("payload", [
    {"collection_id": DEV, "query": "   "},
    {"collection_id": DEV, "query": "x" * 501},
    {"collection_id": "../etc/passwd", "query": "hi"},
    {"collection_id": DEV, "query": "hi", "top_k": 0},
    {"collection_id": DEV, "query": "hi", "top_k": 11},
    {"collection_id": DEV, "query": "hi", "retrieval_mode": "sql"},
    {"query": "hi"},
])
def test_demo_rejects_malformed_input(client, payload):
    assert client.post("/demo/ask", json=payload).status_code == 422


def test_demo_ingest_rejects_arbitrary_targets(client):
    r = client.post("/demo/ingest", json={"collection_id": "http://169.254.169.254/latest", "scenario": "unchanged"})
    assert r.status_code == 422
    r = client.post("/demo/ingest", json={"collection_id": DEV, "scenario": "unchanged", "url": "http://evil"})
    assert r.status_code == 200 and "url" not in r.json()


def test_body_size_limit(client):
    r = client.post("/demo/ask", content=b'{"q":"' + b"a" * (settings.MAX_REQUEST_BYTES + 10) + b'"}',
                    headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_security_headers(client):
    r = client.get("/health/")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-request-id"]


def test_rate_limit_returns_429_with_retry_after(client, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_QUERY_PER_MINUTE", 3)
    payload = {"collection_id": DEV, "query": "rate limit"}
    codes = [client.post("/demo/retrieve", json=payload).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = client.post("/demo/retrieve", json=payload)
    assert int(r.headers["retry-after"]) >= 1


@pytest.mark.parametrize("method,path", [
    ("post", "/ingest/document360"),
    ("post", "/ingest/sharepoint"),
    ("get", "/ingest/jobs"),
    ("post", "/debug/trace"),
    ("post", "/feedback/"),
])
def test_protected_operations_require_service_key(client, method, path):
    assert client.request(method.upper(), path, json={}).status_code == 401
    assert client.request(method.upper(), path, json={}, headers={"X-API-Key": "wrong"}).status_code == 401


def test_protected_operations_disabled_without_configured_key(client, monkeypatch):
    monkeypatch.setattr(settings, "SERVICE_API_KEY", "")
    assert client.post("/ingest/document360", headers={"X-API-Key": ""}).status_code == 403


def test_live_queries_require_key_by_default(client):
    r = client.post("/retrieve/", json={"query": "sso"})
    assert r.status_code == 401
    assert "/demo" in r.json()["detail"]


def test_live_query_without_database_is_503_not_fabricated(client):
    r = client.post("/ask/", json={"query": "sso"}, headers={"X-API-Key": SERVICE_KEY})
    assert r.status_code == 503
    assert "mode" not in r.json()


def test_ingest_unconfigured_connector_is_503(client, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:1/x")
    monkeypatch.setattr(settings, "DOCUMENT360_API_KEY", "")
    r = client.post("/ingest/document360", headers={"X-API-Key": SERVICE_KEY})
    assert r.status_code == 503
    assert "DOCUMENT360_API_KEY" in r.json()["detail"]


def test_anonymous_acl_groups_are_ignored(client, monkeypatch):
    """Client-supplied acl_groups must not widen access for anonymous callers."""
    captured = {}

    async def fake_retrieve(db, query, acl_groups, top_k, diagnostics):
        captured["acl_groups"] = acl_groups
        return {"query": query, "chunks": [], "total_retrieved": 0}

    async def fake_db():
        yield None

    from api.main import app
    from api.routes import retrieval
    from core.database import get_db

    monkeypatch.setattr(retrieval, "retrieve", fake_retrieve)
    monkeypatch.setattr(settings, "ALLOW_ANONYMOUS_LIVE_QUERIES", True)
    app.dependency_overrides[get_db] = fake_db
    try:
        client.post("/retrieve/", json={"query": "salary", "acl_groups": ["hr", "admins"]})
        assert captured["acl_groups"] == []
        client.post("/retrieve/", json={"query": "salary", "acl_groups": ["hr"]}, headers={"X-API-Key": SERVICE_KEY})
        assert captured["acl_groups"] == ["hr"]
    finally:
        app.dependency_overrides.clear()


def test_integration_status_without_config(client):
    body = client.get("/health/integrations").json()
    states = {i["key"]: i["state"] for i in body["integrations"]}
    assert states == {
        "document360": "unavailable", "microsoft_graph": "unavailable", "postgres": "unavailable",
        "pgvector": "unavailable", "embeddings": "unavailable", "object_storage": "unavailable", "demo_kb": "demo",
    }
    assert body["live_query_ready"] is False


def test_configured_but_unreachable_is_disconnected_not_connected(client, monkeypatch):
    """Configuration alone must never produce 'connected'."""
    monkeypatch.setattr(settings, "INTEGRATION_CHECK_TIMEOUT_SECONDS", 2.0)
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:1/x")
    monkeypatch.setattr(settings, "DOCUMENT360_API_KEY", "token")
    monkeypatch.setattr(settings, "DOCUMENT360_PROJECT_ID", "proj")
    monkeypatch.setattr(settings, "DOCUMENT360_BASE_URL", "http://127.0.0.1:1/v2")
    import core.database as database
    monkeypatch.setattr(database, "_engine", None)
    body = client.get("/health/integrations").json()
    states = {i["key"]: i["state"] for i in body["integrations"]}
    assert states["postgres"] == "disconnected"
    assert states["document360"] == "disconnected"
    monkeypatch.setattr(database, "_engine", None)


def test_responses_never_contain_secret_values(client, monkeypatch):
    for k, v in SECRET_VALUES.items():
        monkeypatch.setattr(settings, k, v)
    monkeypatch.setattr(settings, "INTEGRATION_CHECK_TIMEOUT_SECONDS", 2.0)
    monkeypatch.setattr(settings, "S3_ENDPOINT_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(settings, "DOCUMENT360_PROJECT_ID", "proj")
    monkeypatch.setattr(settings, "DOCUMENT360_BASE_URL", "http://127.0.0.1:1/v2")
    import core.database as database
    monkeypatch.setattr(database, "_engine", None)
    responses = [
        client.get("/health/integrations"),
        client.get("/health/ready"),
        client.get("/openapi.json"),
        client.post("/demo/ask", json={"collection_id": DEV, "query": "api keys"}),
        client.post("/ask/", json={"query": "x"}, headers={"X-API-Key": SERVICE_KEY}),
    ]
    for r in responses:
        for secret in SECRET_VALUES.values():
            assert secret not in r.text
        assert "SECRET" not in r.text
        assert "Traceback" not in r.text
    monkeypatch.setattr(database, "_engine", None)


def test_unhandled_errors_are_opaque(client, monkeypatch):
    from demo import engine

    def boom(*a, **k):
        raise RuntimeError("internal detail: /etc/secret path")

    monkeypatch.setattr(engine, "answer", boom)
    from fastapi.testclient import TestClient
    from api.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post("/demo/ask", json={"collection_id": DEV, "query": "hi"})
    assert r.status_code == 500
    assert "internal detail" not in r.text and r.json()["request_id"]


def test_cors_allows_only_configured_origins(client):
    ok = client.options("/demo/ask", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.options("/demo/ask", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in bad.headers


def test_allowed_origins_parsing(monkeypatch):
    monkeypatch.setattr(settings, "ALLOWED_ORIGINS", "https://a.example, https://b.example")
    assert settings.allowed_origins == ["https://a.example", "https://b.example"]
    monkeypatch.setattr(settings, "ALLOWED_ORIGINS", '["https://c.example"]')
    assert settings.allowed_origins == ["https://c.example"]
