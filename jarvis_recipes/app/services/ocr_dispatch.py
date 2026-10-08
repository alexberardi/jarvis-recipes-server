"""Hand a photo import's images to OCR, by whichever transport is configured.

`ocr.transport` (settings DB):
  - "http"  -- jarvisd's POST /v1/ocr/jobs with a callback (the default). One
               jarvisd answers once, so the join expects one reading.
  - "redis" -- the legacy fan-out onto every queue in OCR_QUEUES for the Python
               jarvis-ocr-service workers. Kept so a stack that has not cut over
               to jarvisd keeps importing photos; delete it once nothing uses it.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from jarvis_recipes.app.db import models
from jarvis_recipes.app.services import ocr_jobs_client, queue_service
from jarvis_recipes.app.services.ocr_jobs_client import OcrImage, OcrSubmitError
from jarvis_recipes.app.services.settings_service import (
    DEFAULT_OCR_TRANSPORT,
    OCR_TRANSPORT_HTTP,
    OCR_TRANSPORT_REDIS,
    get_settings_service,
)

logger = logging.getLogger(__name__)


def transport() -> str:
    return (get_settings_service().get_str("ocr.transport", DEFAULT_OCR_TRANSPORT) or DEFAULT_OCR_TRANSPORT).strip().lower()


def dispatch(
    db: Session,
    job: models.RecipeParseJob,
    images: list[OcrImage],
    image_refs: list[dict[str, Any]],
    language: str = "en",
) -> int:
    """Send the images to OCR. Returns how many readings to wait for.

    Raises OcrSubmitError when nothing could be queued; the caller fails the job.
    """
    mode = transport()
    if mode == OCR_TRANSPORT_REDIS:
        return queue_service.enqueue_ocr_request(
            workflow_id=job.id,
            job_id=job.id,
            image_refs=image_refs,
            options={"language": language},
            request_id=None,
        )
    if mode != OCR_TRANSPORT_HTTP:
        raise OcrSubmitError(f"Unknown ocr.transport {mode!r}; expected 'http' or 'redis'")

    ocr_job_id = ocr_jobs_client.submit_job(images, workflow_id=job.id, language=language)
    # Recorded so the callback can prove it answers THIS submission: only we and
    # jarvisd know the id, and a completion naming another one is refused.
    job.job_data = {**(job.job_data or {}), "ocr_job_id": ocr_job_id, "ocr_transport": OCR_TRANSPORT_HTTP}
    db.commit()
    return 1
