#!/usr/bin/env python
"""
Simple polling worker to process queued recipe parse jobs.

Run manually:
    python scripts/run_parse_worker.py
"""
import asyncio
import logging
import uuid
import time

from sqlalchemy.orm import Session

from jarvis_recipes.app.core.config import get_settings
from jarvis_recipes.app.db.session import SessionLocal
from jarvis_recipes.app.services import parse_job_service, url_recipe_parser
from jarvis_recipes.app.services import meal_plan_service
from jarvis_recipes.app.schemas.meal_plan import MealPlanGenerateRequest
from jarvis_recipes.app.schemas.ingestion_input import IngestionInput
from jarvis_recipes.app.services.ingestion_service import parse_recipe as parse_recipe_ingestion
from jarvis_recipes.app.services.image_ingest_worker import process_image_ingestion_job

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("parse_worker")

POLL_INTERVAL_SECONDS = 5


def process_one(db: Session) -> bool:
    settings = get_settings()
    max_retries = settings.llm_recipe_queue_max_retries
    job = (
        parse_job_service.fetch_next_pending(db, job_type="ingestion")
        or parse_job_service.fetch_next_pending(db, job_type="image")
        or parse_job_service.fetch_next_pending(db, job_type="meal_plan_generate")
        or parse_job_service.fetch_next_pending(db, job_type="url")
    )
    if not job:
        return False
    logger.info("Processing job %s (%s)", job.id, job.job_type)
    parse_job_service.mark_running(db, job)

    if job.job_type == "image":
        try:
            asyncio.run(process_image_ingestion_job(db, job))
            return True
        except Exception as exc:  # noqa: BLE001
            logger.exception("Image job %s crashed", job.id)
            parse_job_service.mark_error(db, job, "worker_error", str(exc))
            return True

    if job.job_type == "ingestion":
        try:
            input_payload = IngestionInput.model_validate(job.job_data or {})
        except Exception as exc:  # noqa: BLE001
            parse_job_service.mark_error(db, job, "invalid_payload", str(exc))
            return True
        try:
            result = asyncio.run(parse_recipe_ingestion(input_payload))
            if result.success:
                parse_job_service.mark_complete(db, job, result)
                logger.info("Job %s complete", job.id)
            else:
                # Don't retry encoding errors - they won't fix themselves
                # Also don't retry if next_action is set (client should handle it)
                is_encoding_error = (
                    result.error_code == "fetch_failed" 
                    and ("encoding_error" in (result.warnings or []) or result.next_action == "webview_extract")
                )
                should_retry = (
                    not is_encoding_error 
                    and result.next_action is None
                    and job.attempts < max_retries 
                    and (result.error_code in {"llm_timeout", "llm_failed", "fetch_failed"})
                )
                if should_retry:
                    logger.warning("Job %s failed with %s; retrying (attempt %s/%s)", job.id, result.error_code, job.attempts, max_retries)
                    job.status = parse_job_service.RecipeParseJobStatus.PENDING.value
                    job.error_code = result.error_code
                    job.error_message = result.error_message
                    db.commit()
                    db.refresh(job)
                else:
                    # If result has next_action, store the full result so client can see the suggestion
                    if result.next_action:
                        # Store result_json with next_action even though it failed
                        parse_job_service.mark_complete(db, job, result)
                        # Override status to ERROR but keep result_json
                        job.status = parse_job_service.RecipeParseJobStatus.ERROR.value
                        job.error_code = result.error_code or "parse_failed"
                        job.error_message = result.error_message or "Parse failed"
                        db.commit()
                        logger.warning("Job %s failed but stored next_action=%s: %s", job.id, result.next_action, result.error_message)
                    else:
                        parse_job_service.mark_error(db, job, result.error_code or "parse_failed", result.error_message or "Parse failed")
                        logger.warning("Job %s failed: %s", job.id, result.error_message)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Job %s crashed", job.id)
            parse_job_service.mark_error(db, job, "worker_error", str(exc))
        return True

    if job.job_type == "meal_plan_generate":
        try:
            job_data = job.job_data or {}
            request_id = job_data.get("request_id") or str(uuid.uuid4())
            payload = job_data.get("payload") or {}
            req = MealPlanGenerateRequest.model_validate(payload)
        except Exception as exc:  # noqa: BLE001
            parse_job_service.mark_error(db, job, "invalid_payload", str(exc))
            return True
        try:
            result, slot_failures = meal_plan_service.generate_meal_plan(db, job.user_id, req, request_id)
            meal_plan_service.publish_completed(db, job.user_id, request_id, result, slot_failures)
            job.result_json = {"result": result.model_dump(mode="json"), "slot_failures_count": slot_failures}
            job.status = parse_job_service.RecipeParseJobStatus.COMPLETE.value
            db.commit()
            db.refresh(job)
        except Exception as exc:  # noqa: BLE001
            meal_plan_service.publish_failed(db, job.user_id, request_id, "generation_failed", str(exc))
            parse_job_service.mark_error(db, job, "generation_failed", str(exc))
        return True

    try:
        result = asyncio.run(url_recipe_parser.parse_recipe_from_url(job.url, job.use_llm_fallback))
        if result.success:
            parse_job_service.mark_complete(db, job, result)
            logger.info("Job %s complete", job.id)
        else:
            should_retry = job.attempts < max_retries and (result.error_code in {"llm_timeout", "llm_failed", "fetch_failed"})
            if should_retry:
                logger.warning("Job %s failed with %s; retrying (attempt %s/%s)", job.id, result.error_code, job.attempts, max_retries)
                job.status = parse_job_service.RecipeParseJobStatus.PENDING.value
                job.error_code = result.error_code
                job.error_message = result.error_message
                db.commit()
                db.refresh(job)
            else:
                parse_job_service.mark_error(db, job, result.error_code or "parse_failed", result.error_message or "Parse failed")
                logger.warning("Job %s failed: %s", job.id, result.error_message)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Job %s crashed", job.id)
        parse_job_service.mark_error(db, job, "worker_error", str(exc))
    return True


def main():
    settings = get_settings()
    cleanup_interval = 60  # seconds
    last_cleanup = 0
    while True:
        with SessionLocal() as db:
            worked = process_one(db)
            now = time.time()
            if now - last_cleanup > cleanup_interval:
                try:
                    abandoned = parse_job_service.abandon_stale_jobs(db, settings.recipe_parse_job_abandon_minutes)
                    if abandoned:
                        logger.info("Marked %s jobs as ABANDONED", abandoned)
                    cleaned, abandoned_jobs = meal_plan_service.cleanup_expired_stage_recipes(db, cutoff_hours=72, mark_jobs=True)
                    if cleaned:
                        logger.info("Deleted %s expired stage recipes (abandoned %s jobs)", cleaned, abandoned_jobs)
                except Exception:
                    logger.exception("Cleanup (abandon) failed")
                last_cleanup = now
        if not worked:
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()

