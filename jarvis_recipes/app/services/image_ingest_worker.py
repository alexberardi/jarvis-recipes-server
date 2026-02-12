import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from jarvis_recipes.app.db import models

from jarvis_recipes.app.core.config import get_settings
from jarvis_recipes.app.db import models
from jarvis_recipes.app.schemas.ingestion import RecipeDraft
from jarvis_recipes.app.services import mailbox_service, parse_job_service, s3_storage
from jarvis_recipes.app.services.image_ingest_pipeline import OCRServiceUnavailableError, run_ingestion_pipeline

logger = logging.getLogger(__name__)


async def _load_images_from_s3(keys: List[str]) -> List[bytes]:
    loop = asyncio.get_event_loop()
    return [await loop.run_in_executor(None, s3_storage.download_image, key) for key in keys]


# Vision processing is now handled by the OCR service via llm_proxy_vision provider
async def _run_pipeline_and_record(
    db: Session, ingestion: models.RecipeIngestion, image_bytes: List[bytes], tier_max: int, job: models.RecipeParseJob
) -> Tuple[bool, Optional[RecipeDraft], Dict[str, Any]]:
    settings = get_settings()
    pipeline_json: Dict[str, Any] = {}
    try:
        draft, pipeline_json, texts = await run_ingestion_pipeline(ingestion, image_bytes, tier_max)
    except OCRServiceUnavailableError as exc:
        # OCR service unavailable - mark job as pending for retry
        logger.warning("OCR service unavailable for ingestion %s: %s. Marking job for retry.", ingestion.id, exc)
        ingestion.status = "PENDING"
        pipeline_json = pipeline_json or {"error": str(exc), "retry_reason": "ocr_service_unavailable"}
        ingestion.pipeline_json = pipeline_json
        db.commit()
        db.refresh(ingestion)
        # Mark job as pending (attempts already incremented by mark_running)
        job.status = "PENDING"
        job.error_code = "ocr_service_unavailable"
        job.error_message = str(exc)
        db.commit()
        db.refresh(job)
        return False, None, pipeline_json
    except Exception as exc:  # noqa: BLE001
        logger.exception("Pipeline failed for ingestion %s: %s", ingestion.id, exc)
        ingestion.status = "FAILED"
        pipeline_json = {"error": str(exc)}
        ingestion.pipeline_json = pipeline_json
        db.commit()
        db.refresh(ingestion)
        mailbox_service.publish(
            db,
            ingestion.user_id,
            "recipe_image_ingestion_failed",
            {
                "ingestion_id": ingestion.id,
                "error_code": "pipeline_failed",
                "message": "Failed to extract recipe from images",
            },
        )
        return False, None, pipeline_json

    attempts = pipeline_json.get("attempts", []) if isinstance(pipeline_json, dict) else []

    # Note: Vision and Cloud OCR are now handled by the OCR service
    # via llm_proxy_vision and llm_proxy_cloud providers in the "auto" selection

    if draft:
        pipeline_json["warnings"] = pipeline_json.get("warnings") or []
        ingestion.status = "SUCCEEDED"
        ingestion.selected_tier = pipeline_json.get("selected_tier")
        ingestion.tier1_text = texts.get("tier1_text")
        ingestion.tier2_text = texts.get("tier2_text")
        ingestion.pipeline_json = pipeline_json
        db.commit()
        db.refresh(ingestion)
        mailbox_service.publish(
            db,
            ingestion.user_id,
            "recipe_image_ingestion_completed",
            {
                "ingestion_id": ingestion.id,
                "recipe_draft": draft.model_dump(),
                "pipeline": pipeline_json,
            },
        )
        return True, draft, pipeline_json
    else:
        ingestion.status = "FAILED"
        ingestion.pipeline_json = pipeline_json
        db.commit()
        db.refresh(ingestion)
        mailbox_service.publish(
            db,
            ingestion.user_id,
            "recipe_image_ingestion_failed",
            {
                "ingestion_id": ingestion.id,
                "error_code": "ocr_failed",
                "message": "OCR quality insufficient to extract recipe",
            },
        )
        return False, None, pipeline_json


async def process_image_ingestion_job(db: Session, job: models.RecipeParseJob) -> None:
    payload = job.job_data or {}
    ingestion_id: Optional[str] = payload.get("ingestion_id")
    if not ingestion_id:
        parse_job_service.mark_error(db, job, "invalid_job_data", "Missing ingestion_id")
        return
    ingestion = db.get(models.RecipeIngestion, ingestion_id)
    if not ingestion:
        parse_job_service.mark_error(db, job, "invalid_ingestion", "Ingestion not found")
        return
    ingestion.status = "RUNNING"
    try:
        db.commit()
        db.refresh(ingestion)
    except Exception as commit_exc:
        logger.exception("Failed to update ingestion status to RUNNING for job %s: %s", job.id, commit_exc)
        db.rollback()
        parse_job_service.mark_error(db, job, "database_error", f"Failed to update ingestion status: {commit_exc}")
        return

    try:
        image_bytes = await _load_images_from_s3(ingestion.image_s3_keys or [])
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to load images from S3 for job %s: %s", job.id, exc)
        try:
            db.rollback()
            parse_job_service.mark_error(db, job, "invalid_images", "Unable to load images from storage")
            ingestion.status = "FAILED"
            db.commit()
            db.refresh(ingestion)
            try:
                mailbox_service.publish(
                    db,
                    ingestion.user_id,
                    "recipe_image_ingestion_failed",
                    {
                        "ingestion_id": ingestion.id,
                        "error_code": "invalid_images",
                        "message": "Unable to read uploaded images",
                    },
                )
            except Exception as publish_exc:
                logger.warning("Failed to publish failure message for job %s: %s", job.id, publish_exc)
        except Exception as mark_exc:
            logger.exception("Failed to mark job %s as error after image load failure: %s", job.id, mark_exc)
        return

    tier_max = payload.get("tier_max") or ingestion.tier_max or 3
    try:
        success, final_draft, pipeline_json = await _run_pipeline_and_record(db, ingestion, image_bytes, tier_max, job)
    except Exception as exc:
        logger.exception("Exception in _run_pipeline_and_record for job %s: %s", job.id, exc)
        pipeline_json = {"error": str(exc), "error_code": "pipeline_exception"}
        success = False
        final_draft = None
    
    logger.info(
        "image ingestion job finished",
        extra={"job_id": job.id, "ingestion_id": ingestion.id, "success": success, "status": job.status},
    )
    
    if success and final_draft:
        try:
            # Create a result object that mark_complete can process
            # mark_complete calls result.model_dump_json() and expects JSON with "recipe" or "recipe_draft"
            class ImageParseResult:
                def model_dump_json(self):
                    payload = {
                        "recipe_draft": final_draft.model_dump(),
                        "pipeline": pipeline_json,
                    }
                    return json.dumps(payload)
            
            result = ImageParseResult()
            logger.debug("Calling mark_complete for job %s", job.id)
            parse_job_service.mark_complete(db, job, result)
            logger.info("Successfully marked job %s as complete", job.id)
        except Exception as exc:
            logger.exception("Failed to mark job %s as complete: %s", job.id, exc)
            # Fall back to marking as error with the pipeline info
            try:
                db.rollback()
                job.result_json = {"pipeline": pipeline_json, "recipe_draft": final_draft.model_dump() if final_draft else None}
                db.commit()
                parse_job_service.mark_error(db, job, "mark_complete_failed", str(exc))
            except Exception as exc2:
                logger.exception("Failed to mark job %s as error after mark_complete failed: %s", job.id, exc2)
                try:
                    db.rollback()
                except (OSError, RuntimeError):
                    pass
    else:
        # Store pipeline_json in result_json even on failure so UI can see what happened
        try:
            # Extract error info from pipeline_json, with fallbacks
            if isinstance(pipeline_json, dict):
                error_code = pipeline_json.get("error_code") or pipeline_json.get("status") or "ingestion_failed"
                error_message = (
                    pipeline_json.get("error_message") 
                    or pipeline_json.get("error")
                    or "Image ingestion failed"
                )
                # Check if quality gate failed
                attempts = pipeline_json.get("attempts", [])
                if attempts:
                    last_attempt = attempts[-1] if isinstance(attempts, list) else {}
                    if isinstance(last_attempt, dict):
                        status = last_attempt.get("status", "")
                        if "quality" in status.lower():
                            error_code = "quality_gate_failed"
                            error_message = f"OCR quality insufficient: {error_message}"
                        elif "validation" in status.lower():
                            error_code = "validation_failed"
                            error_message = f"Recipe validation failed: {error_message}"
            else:
                error_code = "ingestion_failed"
                error_message = "Image ingestion failed"
            
            # Set result_json before marking error (mark_error doesn't touch result_json)
            try:
                db.rollback()  # Ensure clean state
                job.result_json = {"pipeline": pipeline_json, "recipe_draft": None}
                db.commit()  # Commit result_json first
                parse_job_service.mark_error(db, job, error_code, error_message)
                logger.info("Marked job %s as error: %s - %s", job.id, error_code, error_message)
            except Exception as commit_exc:
                logger.exception("Failed to commit error state for job %s: %s", job.id, commit_exc)
                try:
                    db.rollback()
                    # Try one more time with just error marking
                    parse_job_service.mark_error(db, job, error_code, error_message)
                except Exception as final_exc:
                    logger.exception("Final attempt to mark job %s as error failed: %s", job.id, final_exc)
        except Exception as exc:
            logger.exception("Failed to mark job %s as error: %s", job.id, exc)
            try:
                db.rollback()
            except (OSError, RuntimeError):
                pass

