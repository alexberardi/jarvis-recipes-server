"""Where jarvisd's OCR delivers a finished photo-import job.

jarvisd POSTs the `ocr.completed` envelope (internal/modules/ocr/jobs.go,
completionMessage) to the callback_url we gave it, signed with its own app
credentials, and retries until it gets a 2xx. This route:

  1. authenticates the caller's app credentials against jarvisd's auth
     (/internal/app-ping) -- the same check every app-to-app route uses;
  2. checks the envelope answers a submission we actually made: the
     RecipeParseJob named by workflow_id must have recorded this ocr_job_id when
     it was submitted (services/ocr_dispatch). App credentials say who is
     calling, not that the caller is the OCR that ran our job;
  3. puts the envelope on our own RQ queue, unchanged. queue_worker handles it
     exactly as it handled the pickled job the Python OCR worker used to enqueue.

A 404 for an unknown or mismatched job makes jarvisd retry for a while; that is
also what covers the narrow race where the completion arrives before the
submitting request has committed the job id.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from jarvis_recipes.app.api.deps import get_db_session, verify_app_auth
from jarvis_recipes.app.db import models
from jarvis_recipes.app.services import queue_service
from jarvis_recipes.app.services.ocr_jobs_client import CALLBACK_PATH
from jarvis_recipes.app.services.parse_job_service import RecipeParseJobStatus

router = APIRouter(tags=["internal"])
logger = logging.getLogger(__name__)

_FINISHED = {
    RecipeParseJobStatus.COMPLETE.value,
    RecipeParseJobStatus.ERROR.value,
    RecipeParseJobStatus.CANCELED.value,
    RecipeParseJobStatus.COMMITTED.value,
    RecipeParseJobStatus.ABANDONED.value,
}


class OcrCompletion(BaseModel):
    """The envelope jarvisd sends. Extra keys (source, target, created_at, ...)
    are kept and forwarded to the queue untouched."""

    model_config = ConfigDict(extra="allow")

    schema_version: int
    job_id: str
    workflow_id: str
    job_type: Literal["ocr.completed"]
    ocr_job_id: str
    payload: dict[str, Any]
    trace: dict[str, Any] | None = None


@router.post(CALLBACK_PATH, status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(verify_app_auth)])
def receive_ocr_completion(
    completion: OcrCompletion,
    db: Session = Depends(get_db_session),
) -> dict[str, str]:
    job = db.get(models.RecipeParseJob, completion.workflow_id)
    if job is None or (job.job_data or {}).get("ocr_job_id") != completion.ocr_job_id:
        logger.warning(
            "OCR callback for unknown job: workflow %s, ocr job %s", completion.workflow_id, completion.ocr_job_id
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown OCR job")

    if job.status in _FINISHED:
        # A redelivery after the worker already finished: acknowledge it so
        # jarvisd stops retrying, and do not re-run a finished job.
        logger.info("OCR callback for finished job %s (%s); ignored", job.id, job.status)
        return {"status": "ignored"}

    try:
        queued = queue_service.enqueue_completion_envelope(completion.model_dump(mode="json"))
    except Exception as exc:  # noqa: BLE001 -- Redis down: make jarvisd retry
        logger.exception("Could not queue OCR completion for job %s", job.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Could not queue the OCR result"
        ) from exc
    return {"status": "queued" if queued else "duplicate"}
