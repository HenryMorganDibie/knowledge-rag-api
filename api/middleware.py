"""
ASGI middleware for public exposure: request IDs, body size limits,
request timeouts, security headers, and non-leaking error responses.
"""

import asyncio
import json
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.config import settings
from core.logger import get_logger

logger = get_logger("api.access")

# Swagger UI / ReDoc load assets from a CDN, so they get a looser CSP.
_DOC_PATHS = ("/docs", "/redoc")
_API_CSP = "default-src 'none'; frame-ancestors 'none'"
_DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com; frame-ancestors 'none'"
)


class _BodyTooLarge(Exception):
    pass


async def _send_json(send: Send, status: int, body: dict, headers=None) -> None:
    payload = json.dumps(body).encode()
    raw_headers = [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]
    for k, v in (headers or {}).items():
        raw_headers.append((k.encode(), v.encode()))
    await send({"type": "http.response.start", "status": status, "headers": raw_headers})
    await send({"type": "http.response.body", "body": payload})


class HardeningMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex[:16]
        path = scope.get("path", "")
        method = scope.get("method", "")
        started = time.perf_counter()
        response_started = False
        status_code = 500
        limit = settings.MAX_REQUEST_BYTES

        headers = dict(scope.get("headers") or [])
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                too_large = int(declared) > limit
            except ValueError:
                too_large = True
            if too_large:
                await _send_json(send, 413, {"detail": f"Request body exceeds {limit} bytes.", "request_id": request_id})
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLarge()
            return message

        is_docs = path.startswith(_DOC_PATHS)

        async def send_with_headers(message: Message) -> None:
            nonlocal response_started, status_code
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                extra = [
                    (b"x-request-id", request_id.encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                    (b"cross-origin-opener-policy", b"same-origin"),
                    (b"content-security-policy", (_DOCS_CSP if is_docs else _API_CSP).encode()),
                ]
                if settings.APP_ENV == "production":
                    extra.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                if not is_docs:
                    extra.append((b"cache-control", b"no-store"))
                message["headers"] = list(message.get("headers", [])) + extra
            await send(message)

        try:
            await asyncio.wait_for(
                self.app(scope, limited_receive, send_with_headers),
                timeout=settings.REQUEST_TIMEOUT_SECONDS,
            )
        except _BodyTooLarge:
            if not response_started:
                status_code = 413
                await _send_json(send, 413, {"detail": f"Request body exceeds {limit} bytes.", "request_id": request_id})
        except asyncio.TimeoutError:
            if not response_started:
                status_code = 504
                await _send_json(send, 504, {"detail": "Request timed out.", "request_id": request_id})
        except Exception:
            # Log the stack trace server-side only; the client gets an opaque ID.
            logger.exception(f"Unhandled error request_id={request_id} {method} {path}")
            if not response_started:
                status_code = 500
                await _send_json(send, 500, {"detail": "Internal server error.", "request_id": request_id})
        finally:
            # Paths and status only: query strings and bodies may contain
            # visitor questions, so they are never logged.
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            logger.info(f"request_id={request_id} {method} {path} status={status_code} ms={elapsed_ms}")
