"""
Image description processor.
Downloads images referenced in documents and generates natural-language
descriptions via GPT-4o vision so diagrams and screenshots are searchable.
"""

import base64
from typing import Optional

import httpx
import openai

from core.config import settings
from core.logger import get_logger

logger = get_logger(__name__)

DESCRIPTION_PROMPT = (
    "Describe this image concisely for a technical knowledge base search index. "
    "Focus on: what is shown, any text visible, the type of diagram or screenshot, "
    "and what technical concept it illustrates. Keep it under 150 words."
)


async def describe_image_from_url(image_url: str) -> Optional[str]:
    """Fetch image from URL and return a searchable text description."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(image_url)
            resp.raise_for_status()
            image_bytes = resp.content
            media_type = resp.headers.get("content-type", "image/png").split(";")[0]
        return await _describe_image_bytes(image_bytes, media_type)
    except Exception as e:
        logger.warning(f"Image description failed for {image_url}: {e}")
        return None


async def describe_image_bytes(image_bytes: bytes, media_type: str = "image/png") -> Optional[str]:
    return await _describe_image_bytes(image_bytes, media_type)


async def _describe_image_bytes(image_bytes: bytes, media_type: str) -> Optional[str]:
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    oai = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    response = await oai.chat.completions.create(
        model="gpt-4o",
        max_tokens=200,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{b64}"},
                    },
                    {"type": "text", "text": DESCRIPTION_PROMPT},
                ],
            }
        ],
    )
    description = response.choices[0].message.content.strip()
    logger.info(f"Generated image description ({len(description)} chars)")
    return description
