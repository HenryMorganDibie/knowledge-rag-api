"""Typed failures that map to actionable HTTP responses instead of opaque 500s."""


class UpstreamUnavailable(RuntimeError):
    """A required external provider (embeddings, LLM) is not configured or failed."""

    def __init__(self, service: str, reason: str):
        super().__init__(f"{service}: {reason}")
        self.service = service
        self.reason = reason
