"""Ingestion job lifecycle and connector behaviour (no network)."""

import uuid

import httpx
import pytest

from api.routes import ingest
from ingestion.connectors import sharepoint
from ingestion.connectors.sharepoint import SharePointConnector


class _Job:
    def __init__(self):
        self.status = "pending"
        self.error_detail = None


class _Session:
    def __init__(self, job):
        self.job, self.commits, self.rollbacks = job, 0, 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, model, job_id):
        return self.job

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_run_sync_records_counts(monkeypatch):
    job = _Job()
    monkeypatch.setattr(ingest, "AsyncSessionLocal", lambda: _Session(job))
    outcomes = iter([{"action": "ingested", "chunks": 4}, {"action": "skipped", "chunks": 0}])

    async def fake_ingest(**kwargs):
        return next(outcomes)

    monkeypatch.setattr(ingest, "ingest_document", fake_ingest)

    async def items():
        for i in range(2):
            yield {"external_id": str(i)}

    await ingest.run_sync(uuid.uuid4(), "document360", items)
    assert job.status == "completed"
    assert (job.documents_scanned, job.documents_changed, job.documents_skipped, job.chunks_created) == (2, 1, 1, 4)


@pytest.mark.asyncio
async def test_run_sync_records_sanitised_failure(monkeypatch):
    job = _Job()
    session = _Session(job)
    monkeypatch.setattr(ingest, "AsyncSessionLocal", lambda: session)

    async def items():
        raise httpx.ConnectError("token=abc123 at https://secret.example")
        yield  # pragma: no cover

    await ingest.run_sync(uuid.uuid4(), "sharepoint", items)
    assert job.status == "failed"
    assert job.error_detail == "ConnectError after 0 documents; see server logs."
    assert session.rollbacks == 1


def test_text_to_html_escapes_markup():
    out = ingest._text_to_html("Intro <script>x</script>\n\nSecond para")
    assert "<script>" not in out and out.count("<p>") == 2


@pytest.mark.asyncio
async def test_graph_pagination_follows_next_link_on_graph_host_only():
    pages = {
        f"{sharepoint.GRAPH_BASE}/p1": {"value": [{"id": "a"}], "@odata.nextLink": f"{sharepoint.GRAPH_BASE}/p2"},
        f"{sharepoint.GRAPH_BASE}/p2": {"value": [{"id": "b"}], "@odata.nextLink": "https://attacker.example/p3"},
    }

    def handler(request):
        return httpx.Response(200, json=pages[str(request.url)])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ids = [i["id"] async for i in SharePointConnector()._list_children(client, {}, f"{sharepoint.GRAPH_BASE}/p1")]
    assert ids == ["a", "b"]


@pytest.mark.asyncio
async def test_graph_token_is_refreshed_after_expiry(monkeypatch):
    calls = []
    clock = [0.0]
    monkeypatch.setattr(sharepoint.time, "monotonic", lambda: clock[0])

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"access_token": f"t{len(calls)}", "expires_in": 3600})

    real = httpx.AsyncClient
    monkeypatch.setattr(sharepoint.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler)))
    c = SharePointConnector()
    assert await c._get_token() == "t1"
    assert await c._get_token() == "t1"
    clock[0] = 3600
    assert await c._get_token() == "t2"
