"""
S3 storage module - now uses object_store abstraction for MinIO/S3 support.

This module provides backwards-compatible functions while using the new
object_store module under the hood for unified MinIO/S3 support.
"""
import logging
from typing import Optional, Tuple

from jarvis_recipes.app.core.config import get_settings
from jarvis_recipes.app.storage import object_store

logger = logging.getLogger(__name__)


def _get_bucket() -> str:
    """Get bucket name, preferring S3_BUCKET over legacy RECIPE_IMAGE_S3_BUCKET."""
    settings = get_settings()
    # Prefer new unified config
    if settings.s3_bucket:
        return settings.s3_bucket
    # Fall back to legacy config for backwards compatibility
    if settings.recipe_image_s3_bucket:
        return settings.recipe_image_s3_bucket
    raise RuntimeError("S3_BUCKET or RECIPE_IMAGE_S3_BUCKET must be configured")


def build_s3_key(user_id: str, ingestion_id: str, index: int, filename: str) -> str:
    """
    Build S3 key with normalized .jpg extension per PRD.
    
    Per PRD s3-to-minio.md: "Normalize all uploaded images to .jpg on ingestion."
    """
    settings = get_settings()
    # Always use .jpg extension per PRD (normalize format)
    return f"{settings.recipe_image_s3_prefix}/{user_id}/{ingestion_id}/{index}.jpg"


def upload_image(user_id: str, ingestion_id: str, index: int, file, data_override: Optional[bytes] = None) -> Tuple[str, str]:
    """
    Upload image to object storage (S3 or MinIO).
    
    Returns:
        Tuple of (key, uri) where key is the object key and uri is the full s3:// URI
    """
    bucket = _get_bucket()
    key = build_s3_key(user_id, ingestion_id, index, getattr(file, "filename", "upload.jpg"))
    data = data_override if data_override is not None else file.file.read()
    
    # Always use image/jpeg content type since we normalize to .jpg
    content_type = "image/jpeg"
    
    try:
        # Use object_store abstraction for unified MinIO/S3 support
        uri = object_store.put_bytes(bucket, key, content_type, data)
        # Return key (without bucket) and full URI
        return key, uri
    except Exception as exc:  # noqa: BLE001
        logger.exception("S3 upload failed for key=%s", key)
        raise


def download_image(key: str) -> bytes:
    """
    Download image from object storage (S3 or MinIO).
    
    Args:
        key: Object key (path within bucket)
    
    Returns:
        Image bytes
    """
    bucket = _get_bucket()
    try:
        return object_store.get_bytes(bucket, key)
    except Exception as exc:  # noqa: BLE001
        logger.exception("S3 download failed for key=%s", key)
        raise

