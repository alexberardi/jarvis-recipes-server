"""
Object store abstraction for S3-compatible storage (AWS S3 and MinIO).

This module provides a unified interface for storing and retrieving objects
from S3-compatible storage, supporting both AWS S3 and MinIO through
configuration.
"""
import logging
from functools import lru_cache

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import ClientError

from jarvis_recipes.app.core.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def _get_s3_client() -> BaseClient:
    """
    Get or create a boto3 S3 client configured for AWS S3 or MinIO.
    
    Configuration is determined by environment variables:
    - S3_ENDPOINT_URL: If set, uses MinIO (or custom S3-compatible endpoint)
    - S3_FORCE_PATH_STYLE: If true, uses path-style addressing (required for MinIO)
    - S3_REGION: AWS region (default: us-east-1)
    - AWS_ACCESS_KEY_ID: Access key
    - AWS_SECRET_ACCESS_KEY: Secret key
    """
    settings = get_settings()
    
    # Configure boto3 Config for path-style addressing if needed
    config_kwargs = {}
    if settings.s3_force_path_style:
        # Force path-style addressing (required for MinIO)
        config_kwargs["config"] = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        )
    
    client_kwargs = {}
    
    # Configure endpoint for MinIO or custom S3-compatible storage
    if settings.s3_endpoint_url:
        client_kwargs["endpoint_url"] = settings.s3_endpoint_url
    
    # Merge config if we created one
    if "config" in config_kwargs:
        client_kwargs["config"] = config_kwargs["config"]
    
    # Configure credentials
    if settings.aws_access_key_id and settings.aws_secret_access_key:
        client_kwargs["aws_access_key_id"] = settings.aws_access_key_id
        client_kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
    
    # Create client with region
    region = settings.s3_region or "us-east-1"
    client = boto3.client("s3", region_name=region, **client_kwargs)
    
    return client


def put_bytes(bucket: str, key: str, content_type: str, data: bytes) -> str:
    """
    Upload bytes to object storage and return the URI.
    
    Args:
        bucket: Bucket name
        key: Object key (path)
        content_type: MIME type (e.g., "image/jpeg")
        data: Bytes to upload
    
    Returns:
        Full URI in format s3://bucket/key
    
    Raises:
        RuntimeError: If upload fails
    """
    try:
        client = _get_s3_client()
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        uri = uri_for(bucket, key)
        logger.debug("Uploaded object to %s", uri)
        return uri
    except ClientError as exc:
        logger.exception("Failed to upload object to s3://%s/%s: %s", bucket, key, exc)
        raise RuntimeError(f"S3 upload failed: {exc}") from exc
    except Exception as exc:
        logger.exception("Unexpected error uploading object to s3://%s/%s: %s", bucket, key, exc)
        raise RuntimeError(f"S3 upload failed: {exc}") from exc


def get_bytes(bucket: str, key: str) -> bytes:
    """
    Download bytes from object storage.
    
    Args:
        bucket: Bucket name
        key: Object key (path)
    
    Returns:
        Object contents as bytes
    
    Raises:
        RuntimeError: If download fails
    """
    try:
        client = _get_s3_client()
        response = client.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()
    except ClientError as exc:
        logger.exception("Failed to download object from s3://%s/%s: %s", bucket, key, exc)
        raise RuntimeError(f"S3 download failed: {exc}") from exc
    except Exception as exc:
        logger.exception("Unexpected error downloading object from s3://%s/%s: %s", bucket, key, exc)
        raise RuntimeError(f"S3 download failed: {exc}") from exc


def uri_for(bucket: str, key: str) -> str:
    """
    Generate a full URI for an object in object storage.
    
    Uses the s3:// scheme for both AWS S3 and MinIO (S3-compatible).
    The actual endpoint is determined by configuration, not the URI.
    
    Args:
        bucket: Bucket name
        key: Object key (path)
    
    Returns:
        Full URI in format s3://bucket/key
    """
    return f"s3://{bucket}/{key}"

