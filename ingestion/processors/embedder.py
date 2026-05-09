"""
Embedding service — wraps OpenAI text-embedding-3-small.
Batches requests to stay within API limits.
"""

from typing import List
import openai

from core.config import settings
from core.logger import get_logger

logger = get_logger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


async def embed_texts(texts: List[str]) -> List[List[float]]:
    """
    Embed a list of texts. Batches into groups of 100 to respect API limits.
    Returns list of embedding vectors in same order as input.
    """
    if not texts:
        return []

    client = _get_client()
    all_embeddings = []
    batch_size = 100

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        response = await client.embeddings.create(
            model=settings.EMBEDDING_MODEL,
            input=batch,
        )
        batch_embeddings = [item.embedding for item in response.data]
        all_embeddings.extend(batch_embeddings)
        logger.info(f"Embedded batch {i // batch_size + 1} ({len(batch)} texts)")

    return all_embeddings


async def embed_query(query: str) -> List[float]:
    """Embed a single query string."""
    results = await embed_texts([query])
    return results[0]
