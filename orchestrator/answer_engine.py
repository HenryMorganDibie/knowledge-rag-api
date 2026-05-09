"""
Orchestrator API — grounded answer generation.

Takes a user query, retrieves relevant chunks, and generates an answer
grounded strictly in retrieved content with structured citation blocks.
Citations include chunk_id, source title, section path, and a presigned
S3 URL for the source document.
"""

from typing import List, Optional
import openai

from core.config import settings
from core.logger import get_logger
from retrieval.hybrid_retriever import retrieve, RetrievedChunk
from storage.s3_client import generate_presigned_url

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are a precise technical assistant answering questions from an internal knowledge base.

Rules:
1. Answer ONLY using the provided context chunks. Do not use prior knowledge.
2. Every factual claim must be grounded in a specific chunk.
3. At the end of your answer, include a CITATIONS section listing each chunk you referenced.
4. If the context does not contain enough information, say so clearly.
5. Be concise and technically precise."""

CONTEXT_TEMPLATE = """--- CHUNK {index} ---
Source: {title}
Section: {section_path}
Chunk ID: {chunk_id}

{content}
"""


async def answer(
    db,
    query: str,
    acl_groups: Optional[List[str]] = None,
    top_k: int = None,
) -> dict:
    """
    Full RAG pipeline: retrieve → prompt → generate → structure citations.
    Returns answer text + structured citation blocks.
    """
    top_k = top_k or settings.RETRIEVAL_FINAL_TOP_K

    # Retrieve
    retrieval_result = await retrieve(
        db=db,
        query=query,
        acl_groups=acl_groups,
        top_k=top_k,
        diagnostics=False,
    )
    chunks = retrieval_result["chunks"]

    if not chunks:
        return {
            "answer": "I could not find relevant information in the knowledge base for your query.",
            "citations": [],
            "chunks_used": 0,
        }

    # Build context string
    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        context_parts.append(CONTEXT_TEMPLATE.format(
            index=i,
            title=chunk.get("section_path", "Unknown"),
            section_path=chunk.get("section_path", ""),
            chunk_id=chunk["chunk_id"],
            content=chunk["content"],
        ))
    context = "\n".join(context_parts)

    user_message = f"CONTEXT:\n{context}\n\nQUESTION: {query}"

    # Generate answer
    oai = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    response = await oai.chat.completions.create(
        model=settings.LLM_MODEL,
        max_tokens=settings.LLM_MAX_TOKENS,
        temperature=settings.LLM_TEMPERATURE,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )
    answer_text = response.choices[0].message.content.strip()

    # Build structured citation blocks
    citations = []
    for chunk in chunks:
        citation = {
            "chunk_id": chunk["chunk_id"],
            "section_path": chunk.get("section_path", ""),
            "heading": chunk.get("heading", ""),
            "excerpt": chunk["content"][:200] + "..." if len(chunk["content"]) > 200 else chunk["content"],
        }
        citations.append(citation)

    return {
        "answer": answer_text,
        "citations": citations,
        "chunks_used": len(chunks),
        "model": settings.LLM_MODEL,
    }
