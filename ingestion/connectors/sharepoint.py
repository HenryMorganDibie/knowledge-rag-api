"""
SharePoint connector via Microsoft Graph API.
Fetches files from a SharePoint site, extracts text content,
and preserves ACL group metadata for retrieval-time filtering.
"""

import httpx
from typing import AsyncIterator
from dataclasses import dataclass, field

from core.config import settings
from core.logger import get_logger
from storage.s3_client import compute_fingerprint

logger = get_logger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_URL = f"https://login.microsoftonline.com/{settings.AZURE_TENANT_ID}/oauth2/v2.0/token"


@dataclass
class SharePointFile:
    external_id: str        # Graph driveItem ID
    title: str
    content: str            # extracted text
    url: str
    file_type: str          # pdf, docx, html, etc.
    acl_groups: list = field(default_factory=list)
    raw_bytes: bytes = b""


class SharePointConnector:
    """
    Pulls files from a SharePoint document library via Microsoft Graph.
    Supports PDF, DOCX, HTML, and TXT files.
    Preserves SharePoint permission groups as ACL metadata.
    """

    def __init__(self):
        self._token: str | None = None

    async def _get_token(self) -> str:
        if self._token:
            return self._token
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": settings.AZURE_CLIENT_ID,
                    "client_secret": settings.AZURE_CLIENT_SECRET,
                    "scope": "https://graph.microsoft.com/.default",
                },
            )
            resp.raise_for_status()
            self._token = resp.json()["access_token"]
        return self._token

    async def _headers(self) -> dict:
        token = await self._get_token()
        return {"Authorization": f"Bearer {token}"}

    async def fetch_all_files(self) -> AsyncIterator[SharePointFile]:
        """Walk the SharePoint drive and yield supported document files."""
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=60) as client:
            # Get root drive items
            resp = await client.get(
                f"{GRAPH_BASE}/sites/{settings.SHAREPOINT_SITE_ID}/drive/root/children",
                headers=headers,
            )
            resp.raise_for_status()
            items = resp.json().get("value", [])

            for item in items:
                if "file" in item:
                    sp_file = await self._process_file(client, headers, item)
                    if sp_file:
                        yield sp_file
                elif "folder" in item:
                    async for f in self._walk_folder(client, headers, item["id"]):
                        yield f

    async def _walk_folder(
        self, client: httpx.AsyncClient, headers: dict, folder_id: str
    ) -> AsyncIterator[SharePointFile]:
        resp = await client.get(
            f"{GRAPH_BASE}/sites/{settings.SHAREPOINT_SITE_ID}/drive/items/{folder_id}/children",
            headers=headers,
        )
        resp.raise_for_status()
        for item in resp.json().get("value", []):
            if "file" in item:
                f = await self._process_file(client, headers, item)
                if f:
                    yield f
            elif "folder" in item:
                async for nested in self._walk_folder(client, headers, item["id"]):
                    yield nested

    async def _process_file(
        self, client: httpx.AsyncClient, headers: dict, item: dict
    ) -> SharePointFile | None:
        name = item.get("name", "")
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in {"pdf", "docx", "html", "htm", "txt"}:
            return None

        # Download raw bytes
        dl_url = item.get("@microsoft.graph.downloadUrl") or item.get("downloadUrl")
        if not dl_url:
            return None

        raw_resp = await client.get(dl_url)
        raw_bytes = raw_resp.content

        # Extract text
        text = await self._extract_text(raw_bytes, ext)

        # Fetch permissions (ACL)
        acl_groups = await self._get_acl_groups(client, headers, item["id"])

        return SharePointFile(
            external_id=item["id"],
            title=name,
            content=text,
            url=item.get("webUrl", ""),
            file_type=ext,
            acl_groups=acl_groups,
            raw_bytes=raw_bytes,
        )

    async def _extract_text(self, raw_bytes: bytes, ext: str) -> str:
        """Extract plain text from PDF, DOCX, HTML, or TXT."""
        if ext == "txt":
            return raw_bytes.decode("utf-8", errors="replace")
        if ext in {"html", "htm"}:
            from bs4 import BeautifulSoup
            return BeautifulSoup(raw_bytes, "html.parser").get_text(separator="\n")
        if ext == "docx":
            import docx2txt, io
            return docx2txt.process(io.BytesIO(raw_bytes))
        if ext == "pdf":
            import pdfplumber, io
            text_parts = []
            with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
                for page in pdf.pages:
                    text_parts.append(page.extract_text() or "")
            return "\n".join(text_parts)
        return ""

    async def _get_acl_groups(
        self, client: httpx.AsyncClient, headers: dict, item_id: str
    ) -> list:
        """Return list of permission group names for this item."""
        try:
            resp = await client.get(
                f"{GRAPH_BASE}/sites/{settings.SHAREPOINT_SITE_ID}"
                f"/drive/items/{item_id}/permissions",
                headers=headers,
            )
            groups = []
            for perm in resp.json().get("value", []):
                granted = perm.get("grantedToV2") or perm.get("grantedTo", {})
                group = granted.get("group", {})
                if group.get("displayName"):
                    groups.append(group["displayName"])
            return groups
        except Exception as e:
            logger.warning(f"ACL fetch failed for {item_id}: {e}")
            return []

    def fingerprint(self, file: SharePointFile) -> str:
        return compute_fingerprint(file.raw_bytes)
