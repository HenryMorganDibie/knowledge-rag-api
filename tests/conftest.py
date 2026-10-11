"""Shared fixtures: an API client with deterministic, credential-free settings."""

import os

# Neutralise ambient credentials before settings load, so tests never depend
# on (or call) whatever the host environment happens to provide.
for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "OPENAI_API_KEY", "DATABASE_URL",
            "DOCUMENT360_API_KEY", "AZURE_CLIENT_SECRET", "SERVICE_API_KEY"):
    os.environ.pop(var, None)
os.environ.setdefault("RERANKER_ENABLED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core import integrations  # noqa: E402
from core.config import settings  # noqa: E402
from core.security import limiter  # noqa: E402

SERVICE_KEY = "test-service-key-0123456789"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "SERVICE_API_KEY", SERVICE_KEY)
    monkeypatch.setattr(settings, "DATABASE_URL", "")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(settings, "AWS_ACCESS_KEY_ID", "")
    monkeypatch.setattr(settings, "AWS_SECRET_ACCESS_KEY", "")
    monkeypatch.setattr(settings, "S3_ENDPOINT_URL", "")
    limiter.reset()
    integrations.clear_cache()
    from api.main import app

    with TestClient(app) as c:
        yield c
    limiter.reset()
    integrations.clear_cache()
