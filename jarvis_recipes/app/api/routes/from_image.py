import uuid
from typing import List, Optional

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from jarvis_recipes.app.api.deps import get_current_user, get_db_session
from jarvis_recipes.app.core.config import get_settings
from jarvis_recipes.app.db import models
from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.services import parse_job_service, queue_service, s3_storage
from io import BytesIO

# Enable HEIC/HEIF support if pillow-heif is installed.
try:  # pragma: no cover
    import pillow_heif

    pillow_heif.register_heif_opener()  # type: ignore[attr-defined]
except Exception:
    pass

router = APIRouter(tags=["recipes"])
logger = logging.getLogger(__name__)


@router.post("/recipes/from-image/jobs", status_code=status.HTTP_202_ACCEPTED)
async def submit_recipe_from_image_job(
    images: List[UploadFile] = File(...),
    title_hint: Optional[str] = None,
    tier_max: int = 3,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    settings = get_settings()
    if not images or len(images) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No images provided")
    if len(images) > 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Too many images (max 8)")
    if settings.recipe_image_max_bytes:
        for f in images:
            data = await f.read()
            if len(data) > settings.recipe_image_max_bytes:
                raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Image too large")
            f.file.seek(0)

    ingestion_id = str(uuid.uuid4())
    s3_keys = []
    s3_uris = []  # Store full URIs for queue messages
    def _resize_for_vision(data: bytes) -> bytes:
        """
        Resize image to stay within recommended pixel budget for vision models.
        Uses Hugging Face guidance: min_pixels=256*28*28, max_pixels=1280*28*28.
        We do not upscale; we only downscale if over max_pixels.
        """
        MIN_PIXELS = 256 * 28 * 28  # ~200k
        MAX_PIXELS = 1280 * 28 * 28  # ~1.0M

        img = Image.open(BytesIO(data)).convert("RGB")
        w, h = img.size
        pixels = w * h
        if pixels <= MAX_PIXELS:
            # Already within budget; keep original (no upscaling for small images)
            out = BytesIO()
            img.save(out, format="JPEG", quality=90)
            return out.getvalue()

        scale = (MAX_PIXELS / pixels) ** 0.5
        new_w = int(w * scale)
        new_h = int(h * scale)
        # Round to nearest multiple of 28 as suggested for some vision encoders.
        def round28(x: int) -> int:
            return max(28, int(round(x / 28)) * 28)

        new_w = round28(new_w)
        new_h = round28(new_h)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        out = BytesIO()
        img.save(out, format="JPEG", quality=90)
        return out.getvalue()

    for idx, file in enumerate(images):
        try:
            raw = await file.read()
            if not raw:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty image upload")
            try:
                _ = Image.open(BytesIO(raw))
            except UnidentifiedImageError:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unrecognized image file") from None

            resized = _resize_for_vision(raw)
            # Use 0-based index per PRD queue-flow.md (index aligns with OCR multi-image contract)
            key, uri = s3_storage.upload_image(str(current_user.id), ingestion_id, idx, file, data_override=resized)
            s3_keys.append(key)
            s3_uris.append(uri)
            file.file.seek(0)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "from-image upload failed",
                extra={
                    "user_id": str(current_user.id),
                    "ingestion_id": ingestion_id,
                    "file_index": idx + 1,
                    "uploaded_filename": getattr(file, "filename", None),
                },
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to upload images: {exc}",
            ) from exc

    ingestion = models.RecipeIngestion(
        id=ingestion_id,
        user_id=str(current_user.id),
        image_s3_keys=s3_keys,
        status="PENDING",
        tier_max=tier_max,
        title_hint=title_hint,
    )
    db.add(ingestion)
    db.commit()
    db.refresh(ingestion)

    # Create job record for tracking (workflow_id = job_id for simple workflows)
    job = parse_job_service.create_image_job(
        db, str(current_user.id), ingestion_id, job_data={"tier_max": tier_max, "title_hint": title_hint}
    )
    
    # Build image references per PRD queue-flow.md
    # Use URIs returned from upload_image (already in s3://bucket/key format)
    # Per PRD: kind="s3", value is full s3:// URI, index is 0-based
    image_refs = []
    for idx, s3_uri in enumerate(s3_uris):
        image_refs.append({"kind": "s3", "value": s3_uri, "index": idx})
    
    # Enqueue directly to OCR queue (fast-path routing per PRD)
    queue_service.enqueue_ocr_request(
        workflow_id=job.id,  # Use job.id as workflow_id for simple workflows
        job_id=job.id,
        image_refs=image_refs,
        options={"language": "en"},
        request_id=None,  # Could add request_id from headers if available
    )
    
    logger.info(
        "queued from-image job to OCR queue",
        extra={"user_id": str(current_user.id), "ingestion_id": ingestion_id, "job_id": job.id, "images": len(images)},
    )
    return {"ingestion_id": ingestion_id, "job_id": job.id}
