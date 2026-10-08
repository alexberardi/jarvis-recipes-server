"""Submit image OCR to jarvisd's async job API.

jarvisd absorbed jarvis-ocr-service and has no Redis, so the old hand-off --
LPUSH an envelope onto `jarvis.ocr.jobs`, wait for the OCR worker to enqueue a
pickled `ocr.completed` RQ job back onto ours -- has nothing on the other end.
Its replacement is plain HTTP in both directions:

    POST {ocr}/v1/ocr/jobs      images + callback_url   -> 202 {job_id}
    POST {callback_url}         the ocr.completed envelope, signed with
                                jarvisd's app credentials, retried until 2xx

The callback lands on api/routes/ocr_callback.py, which puts the envelope on our
own RQ queue unchanged, so everything downstream (queue_worker, ocr_join) is the
same code that handled the Redis-delivered completion.

The OCR service URL comes from discovery (`jarvis-ocr-service`, which jarvisd
serves); the callback base URL is our own registry row (`jarvis-recipes-server`,
added on jarvisd's Connections page) unless RECIPES_PUBLIC_URL overrides it.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

import httpx

from jarvis_recipes.app.core import service_config
from jarvis_recipes.app.core.config import get_settings

logger = logging.getLogger(__name__)

# jarvisd's POST /v1/ocr/jobs takes 1-8 images.
MAX_IMAGES = 8
CALLBACK_PATH = "/internal/ocr/callback"
SOURCE = "jarvis-recipes-server"


class OcrSubmitError(RuntimeError):
    """The job could not be handed to jarvisd's OCR. Nothing was queued there."""


@dataclass(frozen=True)
class OcrImage:
    data: bytes
    content_type: str = "image/jpeg"


def callback_url() -> str:
    """Where jarvisd should POST the completion.

    It must be reachable FROM jarvisd, which is why it is not derived from the
    incoming request: a phone talks to us through whatever address it has, and
    that is not necessarily one jarvisd can route to.
    """
    base = get_settings().recipes_public_url or service_config.get_recipes_url()
    return base.rstrip("/") + CALLBACK_PATH


def _app_headers() -> dict[str, str]:
    settings = get_settings()
    if not settings.jarvis_app_id or not settings.jarvis_app_key:
        raise OcrSubmitError("JARVIS_APP_ID and JARVIS_APP_KEY must be set to submit OCR jobs")
    return {"X-Jarvis-App-Id": settings.jarvis_app_id, "X-Jarvis-App-Key": settings.jarvis_app_key}


def submit_job(
    images: list[OcrImage],
    *,
    workflow_id: str,
    language: str = "en",
    request_id: str | None = None,
    timeout_seconds: float = 30.0,
) -> str:
    """Queue `images` on jarvisd's OCR and return jarvisd's job id.

    `workflow_id` is our RecipeParseJob id. jarvisd echoes it back as the
    envelope's workflow_id and trace.parent_job_id, which is how queue_worker
    finds the job again.
    """
    if not images:
        raise OcrSubmitError("No images to OCR")
    if len(images) > MAX_IMAGES:
        raise OcrSubmitError(f"At most {MAX_IMAGES} images per OCR job, got {len(images)}")

    try:
        ocr_url = service_config.get_ocr_url()
        target = callback_url()
    except ValueError as exc:
        raise OcrSubmitError(str(exc)) from exc

    body: dict[str, object] = {
        "images": [
            {"content_type": img.content_type, "base64": base64.b64encode(img.data).decode("ascii")}
            for img in images
        ],
        "options": {"language": language},
        "callback_url": target,
        "workflow_id": workflow_id,
        "parent_job_id": workflow_id,
        "source": SOURCE,
    }
    if request_id:
        body["request_id"] = request_id

    url = ocr_url.rstrip("/") + "/v1/ocr/jobs"
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            resp = client.post(url, json=body, headers=_app_headers())
    except httpx.HTTPError as exc:
        raise OcrSubmitError(f"OCR service unreachable at {url}: {exc}") from exc

    if resp.status_code != 202:
        raise OcrSubmitError(f"OCR service refused the job: HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        job_id = resp.json()["job_id"]
    except (ValueError, KeyError, TypeError) as exc:
        raise OcrSubmitError(f"OCR service answered 202 without a job_id: {resp.text[:300]}") from exc

    logger.info("Submitted OCR job %s for workflow %s (%d images), callback %s", job_id, workflow_id, len(images), target)
    return str(job_id)
