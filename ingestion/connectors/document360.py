"""
Document360 ingestion connector.
Fetches articles via the Document360 API v2, computes fingerprints,
and yields only changed documents for re-chunking.
"""

import httpx
from typing import AsyncIterator, Optional
from dataclasses import dataclass, field

from core.config import settings
from core.logger import get_logger
from storage.s3_client import compute_fingerprint

logger = get_logger(__name__)


@dataclass
class D360Article:
    external_id: str
    title: str
    html_content: str
    url: str
    category_path: str        # e.g. "Setup > Installation"
    acl_groups: list = field(default_factory=list)
    raw_bytes: bytes = b""


class Document360Connector:
    """
    Pulls articles from Document360 via REST API.
    Uses fingerprint comparison to skip unchanged documents.
    """

    BASE_URL = settings.DOCUMENT360_BASE_URL

    def __init__(self):
        self.headers = {
            "api_token": settings.DOCUMENT360_API_KEY,
            "Content-Type": "application/json",
        }

    async def fetch_all_articles(self) -> AsyncIterator[D360Article]:
        """Stream all articles from the project, page by page."""
        async with httpx.AsyncClient(timeout=30) as client:
            page = 1
            while True:
                resp = await client.get(
                    f"{self.BASE_URL}/articles",
                    headers=self.headers,
                    params={
                        "projectVersionId": settings.DOCUMENT360_PROJECT_ID,
                        "page": page,
                        "pageSize": 50,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                articles = data.get("data", {}).get("articles", [])
                if not articles:
                    break
                for article in articles:
                    yield await self._fetch_full_article(client, article["id"])
                page += 1

    async def _fetch_full_article(self, client: httpx.AsyncClient, article_id: str) -> D360Article:
        resp = await client.get(
            f"{self.BASE_URL}/articles/{article_id}",
            headers=self.headers,
        )
        resp.raise_for_status()
        a = resp.json().get("data", {})
        html = a.get("html_content", "") or ""
        raw = html.encode("utf-8")
        return D360Article(
            external_id=article_id,
            title=a.get("title", ""),
            html_content=html,
            url=a.get("public_url", ""),
            category_path=self._build_category_path(a),
            acl_groups=a.get("access_groups", []),
            raw_bytes=raw,
        )

    def _build_category_path(self, article: dict) -> str:
        parts = []
        if article.get("category_name"):
            parts.append(article["category_name"])
        if article.get("sub_category_name"):
            parts.append(article["sub_category_name"])
        parts.append(article.get("title", ""))
        return " > ".join(parts)

    def fingerprint(self, article: D360Article) -> str:
        return compute_fingerprint(article.raw_bytes)
