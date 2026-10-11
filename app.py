"""ASGI entrypoint for hosts that discover `app` at the repository root (e.g. Vercel's FastAPI preset)."""

from api.main import app  # noqa: F401
