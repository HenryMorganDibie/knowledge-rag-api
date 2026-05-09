"""
Structure-aware document chunker.
Preserves heading hierarchy and section paths so every chunk
carries its full context (e.g. "Setup > Installation > Windows").
Handles text, tables (serialized to markdown), and image descriptions.
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional

from bs4 import BeautifulSoup

from core.config import settings
from core.logger import get_logger

logger = get_logger(__name__)

HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


@dataclass
class Chunk:
    content: str
    chunk_index: int
    chunk_type: str = "text"          # text | table | image_description
    heading: Optional[str] = None
    section_path: str = ""
    token_count: int = 0
    metadata: dict = field(default_factory=dict)


class StructureAwareChunker:
    """
    Parses HTML content (Document360 articles or SharePoint HTML exports),
    walks the DOM, and produces context-rich chunks that preserve the
    heading hierarchy as a section path.
    """

    def __init__(
        self,
        chunk_size: int = None,
        chunk_overlap: int = None,
    ):
        self.chunk_size = chunk_size or settings.CHUNK_SIZE
        self.chunk_overlap = chunk_overlap or settings.CHUNK_OVERLAP

    def chunk_html(self, html: str, base_title: str = "") -> List[Chunk]:
        soup = BeautifulSoup(html, "html.parser")
        chunks: List[Chunk] = []
        heading_stack: List[str] = [base_title] if base_title else []
        current_text_buffer: List[str] = []
        current_heading: Optional[str] = None
        chunk_index = 0

        def flush_text_buffer():
            nonlocal chunk_index
            text = " ".join(current_text_buffer).strip()
            if not text:
                return
            for sub_chunk in self._split_text(text):
                chunks.append(Chunk(
                    content=sub_chunk,
                    chunk_index=chunk_index,
                    chunk_type="text",
                    heading=current_heading,
                    section_path=self._build_path(heading_stack),
                    token_count=self._estimate_tokens(sub_chunk),
                ))
                chunk_index += 1
            current_text_buffer.clear()

        for element in soup.recursiveChildGenerator():
            tag_name = getattr(element, "name", None)
            if tag_name is None:
                # NavigableString — only collect if not inside a table/img already handled
                parent_name = getattr(element.parent, "name", None)
                if parent_name not in {"table", "tr", "td", "th", "img", "figure"}:
                    text = str(element).strip()
                    if text:
                        current_text_buffer.append(text)
                continue

            # Heading — flush buffer, update stack
            if tag_name in HEADING_TAGS:
                flush_text_buffer()
                heading_text = element.get_text(strip=True)
                level = int(tag_name[1])
                heading_stack = heading_stack[:level - 1] + [heading_text]
                current_heading = heading_text

            # Table — serialize to markdown and emit as a table chunk
            elif tag_name == "table":
                flush_text_buffer()
                table_md = self._table_to_markdown(element)
                if table_md:
                    chunks.append(Chunk(
                        content=table_md,
                        chunk_index=chunk_index,
                        chunk_type="table",
                        heading=current_heading,
                        section_path=self._build_path(heading_stack),
                        token_count=self._estimate_tokens(table_md),
                    ))
                    chunk_index += 1

            # Image — emit placeholder; image description filled in post-processing
            elif tag_name == "img":
                flush_text_buffer()
                alt = element.get("alt", "").strip()
                src = element.get("src", "")
                placeholder = f"[IMAGE: {alt or 'diagram'}] (src: {src})"
                chunks.append(Chunk(
                    content=placeholder,
                    chunk_index=chunk_index,
                    chunk_type="image_description",
                    heading=current_heading,
                    section_path=self._build_path(heading_stack),
                    token_count=self._estimate_tokens(placeholder),
                    metadata={"img_src": src, "img_alt": alt},
                ))
                chunk_index += 1

        flush_text_buffer()
        return chunks

    def chunk_plain_text(self, text: str, title: str = "") -> List[Chunk]:
        """Fallback chunker for plain text (TXT files, extracted PDF text)."""
        chunks = []
        for i, sub in enumerate(self._split_text(text)):
            chunks.append(Chunk(
                content=sub,
                chunk_index=i,
                chunk_type="text",
                heading=title,
                section_path=title,
                token_count=self._estimate_tokens(sub),
            ))
        return chunks

    def _split_text(self, text: str) -> List[str]:
        """
        Recursive character-level split that respects paragraph boundaries.
        Falls back to sentence boundaries, then hard character limit.
        """
        if self._estimate_tokens(text) <= self.chunk_size:
            return [text.strip()] if text.strip() else []

        # Try paragraph split first
        parts = re.split(r"\n{2,}", text)
        if len(parts) > 1:
            return self._merge_parts(parts)

        # Sentence split
        parts = re.split(r"(?<=[.!?])\s+", text)
        if len(parts) > 1:
            return self._merge_parts(parts)

        # Hard cut
        words = text.split()
        result, buf = [], []
        for word in words:
            buf.append(word)
            if self._estimate_tokens(" ".join(buf)) >= self.chunk_size:
                result.append(" ".join(buf))
                buf = buf[-self.chunk_overlap:] if self.chunk_overlap else []
        if buf:
            result.append(" ".join(buf))
        return result

    def _merge_parts(self, parts: List[str]) -> List[str]:
        result, buffer = [], ""
        for part in parts:
            candidate = (buffer + "\n\n" + part).strip() if buffer else part
            if self._estimate_tokens(candidate) <= self.chunk_size:
                buffer = candidate
            else:
                if buffer:
                    result.append(buffer)
                buffer = part
        if buffer:
            result.append(buffer)
        return result

    def _table_to_markdown(self, table_element) -> str:
        rows = table_element.find_all("tr")
        if not rows:
            return ""
        md_rows = []
        for i, row in enumerate(rows):
            cells = row.find_all(["td", "th"])
            cell_texts = [c.get_text(separator=" ", strip=True) for c in cells]
            md_rows.append("| " + " | ".join(cell_texts) + " |")
            if i == 0:
                md_rows.append("| " + " | ".join(["---"] * len(cells)) + " |")
        return "\n".join(md_rows)

    def _build_path(self, stack: List[str]) -> str:
        return " > ".join(s for s in stack if s)

    def _estimate_tokens(self, text: str) -> int:
        # Approximate: 1 token ≈ 4 characters
        return len(text) // 4
