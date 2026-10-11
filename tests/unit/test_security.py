"""Rate limiter and client-IP resolution."""

from starlette.requests import Request

from core.config import settings
from core.security import RateLimiter, client_ip


def test_sliding_window():
    now = [0.0]
    rl = RateLimiter(clock=lambda: now[0])
    assert [rl.check("k", 2) for _ in range(3)] == [None, None, 60]
    now[0] = 60.0
    assert rl.check("k", 2) is None


def _request(xff=None, host="10.0.0.1"):
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "headers": headers, "client": (host, 1234)})


def test_forwarded_for_ignored_unless_trusted(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_FORWARDED_FOR", False)
    assert client_ip(_request("1.2.3.4")) == "10.0.0.1"


def test_forwarded_for_uses_proxy_appended_hop(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_FORWARDED_FOR", True)
    # Left-most entries are client-controlled; the right-most was added by our proxy.
    assert client_ip(_request("6.6.6.6, 203.0.113.9")) == "203.0.113.9"
