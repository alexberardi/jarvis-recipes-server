import logging
import mimetypes
import uuid
from pathlib import Path
from typing import Optional, Tuple

import boto3

from jarvis_recipes.app.core.config import get_settings

logger = logging.getLogger(__name__)


def get_s3_client():
    settings = get_settings()
    session_kwargs = {}
    if settings.aws_access_key_id and settings.aws_secret_access_key:
        session_kwargs["aws_access_key_id"] = settings.aws_access_key_id
        session_kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
    if settings.aws_session_token:
        session_kwargs["aws_session_token"] = settings.aws_session_token
    return boto3.client("s3", region_name=settings.recipe_image_s3_region, **session_kwargs)


def build_s3_key(user_id: str, ingestion_id: str, index: int, filename: str) -> str:
    settings = get_settings()
    ext = Path(filename).suffix.lstrip(".") or "jpg"
    return f"{settings.recipe_image_s3_prefix}/{user_id}/{ingestion_id}/{index}.{ext}"


def upload_image(user_id: str, ingestion_id: str, index: int, file, data_override: Optional[bytes] = None) -> Tuple[str, str]:
    settings = get_settings()
    if not settings.recipe_image_s3_bucket:
        raise RuntimeError("RECIPE_IMAGE_S3_BUCKET is not configured")
    key = build_s3_key(user_id, ingestion_id, index, getattr(file, "filename", "upload.jpg"))
    data = data_override if data_override is not None else file.file.read()
    mime, _ = mimetypes.guess_type(key)
    try:
        client = get_s3_client()
        client.put_object(Bucket=settings.recipe_image_s3_bucket, Key=key, Body=data, ContentType=mime or "image/jpeg")
        return key, f"s3://{settings.recipe_image_s3_bucket}/{key}"
    except Exception as exc:  # noqa: BLE001
        # Log and bubble up for 500 handling upstream
        print(f"S3 upload failed for key={key}: {exc}")
        logger.exception("S3 upload failed for key=%s", key)
        raise


def download_image(key: str) -> bytes:
    settings = get_settings()
    if not settings.recipe_image_s3_bucket:
        raise RuntimeError("RECIPE_IMAGE_S3_BUCKET is not configured")
    client = get_s3_client()
    obj = client.get_object(Bucket=settings.recipe_image_s3_bucket, Key=key)
    return obj["Body"].read()

