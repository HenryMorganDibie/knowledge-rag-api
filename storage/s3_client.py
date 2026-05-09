"""
S3 storage layer — works transparently with real AWS S3 or local MinIO.
Set S3_ENDPOINT_URL in .env to point at MinIO for local dev.
"""

import json
import hashlib
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from core.config import settings
from core.logger import get_logger

logger = get_logger(__name__)


def _get_client():
    kwargs = {
        "region_name": settings.AWS_REGION,
        "aws_access_key_id": settings.AWS_ACCESS_KEY_ID or None,
        "aws_secret_access_key": settings.AWS_SECRET_ACCESS_KEY or None,
    }
    if settings.S3_ENDPOINT_URL:
        kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL
    return boto3.client("s3", **kwargs)


def upload_raw_document(
    content: bytes,
    source_type: str,
    external_id: str,
    filename: str,
    revision: int,
) -> str:
    """Upload raw source file to S3. Returns the S3 key."""
    key = f"raw/{source_type}/{external_id}/rev_{revision}/{filename}"
    client = _get_client()
    client.put_object(
        Bucket=settings.S3_BUCKET_RAW,
        Key=key,
        Body=content,
        Metadata={"source_type": source_type, "external_id": external_id},
    )
    logger.info(f"Uploaded raw doc to s3://{settings.S3_BUCKET_RAW}/{key}")
    return key


def upload_chunk_artifact(chunk_data: dict, source_id: str, chunk_index: int) -> str:
    """Serialize and store chunk JSON artifact. Returns the S3 key."""
    key = f"chunks/{source_id}/chunk_{chunk_index:05d}.json"
    client = _get_client()
    client.put_object(
        Bucket=settings.S3_BUCKET_ARTIFACTS,
        Key=key,
        Body=json.dumps(chunk_data, ensure_ascii=False),
        ContentType="application/json",
    )
    return key


def upload_extracted_image(
    image_bytes: bytes,
    source_id: str,
    image_index: int,
    ext: str = "png",
) -> str:
    """Store an image extracted during document processing."""
    key = f"images/{source_id}/img_{image_index:04d}.{ext}"
    client = _get_client()
    client.put_object(
        Bucket=settings.S3_BUCKET_RAW,
        Key=key,
        Body=image_bytes,
        ContentType=f"image/{ext}",
    )
    return key


def generate_presigned_url(bucket: str, key: str, ttl: int = None) -> str:
    """Generate a time-limited presigned GET URL for secure citation access."""
    ttl = ttl or settings.PRESIGNED_URL_TTL
    client = _get_client()
    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=ttl,
    )
    return url


def compute_fingerprint(content: bytes) -> str:
    """SHA-256 fingerprint for change detection."""
    return hashlib.sha256(content).hexdigest()


def ensure_buckets_exist():
    """Create buckets if they don't exist (local MinIO dev helper)."""
    client = _get_client()
    for bucket in [settings.S3_BUCKET_RAW, settings.S3_BUCKET_ARTIFACTS]:
        try:
            client.head_bucket(Bucket=bucket)
        except ClientError:
            client.create_bucket(Bucket=bucket)
            logger.info(f"Created bucket: {bucket}")
