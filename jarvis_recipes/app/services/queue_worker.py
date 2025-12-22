"""
Worker functions for processing jobs from Redis queue.

This module contains the actual job processing functions that are called
by RQ workers. These functions handle the business logic for each job type.
"""
import asyncio
import json
import logging
import uuid
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from jarvis_recipes.app.core.config import get_settings
from jarvis_recipes.app.db.session import SessionLocal
from jarvis_recipes.app.schemas.ingestion_input import IngestionInput
from jarvis_recipes.app.schemas.meal_plan import MealPlanGenerateRequest
from jarvis_recipes.app.db import models
from jarvis_recipes.app.services import meal_plan_service, parse_job_service, url_recipe_parser
from jarvis_recipes.app.services.image_ingest_worker import process_image_ingestion_job
from jarvis_recipes.app.services.ingestion_service import parse_recipe as parse_recipe_ingestion
from jarvis_recipes.app.services.image_ingest_pipeline import run_ingestion_pipeline
from jarvis_recipes.app.services import ocr_quality
from jarvis_recipes.app.services.llm_client import call_text_structuring, clean_and_validate_draft
from jarvis_recipes.app.core.config import get_settings

logger = logging.getLogger(__name__)


def process_job(payload_json: str) -> None:
    """
    Process a job from the Redis queue.
    
    This is the main entry point for RQ workers. It handles both:
    - New envelope format (per PRD queue-flow.md)
    - Legacy format (for backwards compatibility)
    
    Args:
        payload_json: JSON string containing either envelope or legacy format
    """
    try:
        envelope = json.loads(payload_json)
        
        # Check if this is the new envelope format
        if "schema_version" in envelope and "job_type" in envelope:
            # New envelope format
            job_id = envelope["job_id"]
            workflow_id = envelope.get("workflow_id", job_id)
            job_type = envelope["job_type"]
            payload = envelope.get("payload", {})
            parent_job_id = envelope.get("trace", {}).get("parent_job_id")
            
            logger.info("Processing job %s (%s) from envelope format", job_id, job_type)
            
            with SessionLocal() as db:
                # For ocr.completed events, use workflow_id or parent_job_id to find the original job
                # (job_id is a new UUID created by OCR service)
                if job_type == "ocr.completed":
                    lookup_id = workflow_id or parent_job_id or job_id
                    logger.debug("OCR completion event: looking up job by workflow_id/parent_job_id: %s", lookup_id)
                else:
                    lookup_id = job_id
                
                # Load job from database
                job = db.get(models.RecipeParseJob, lookup_id)
                if not job:
                    logger.error("Job %s not found in database (lookup_id=%s, job_id=%s, workflow_id=%s, parent_job_id=%s)", 
                                lookup_id, lookup_id, job_id, workflow_id, parent_job_id)
                    return
                
                # Check if job was canceled
                if job.status == parse_job_service.RecipeParseJobStatus.CANCELED.value:
                    logger.info("Job %s was canceled, skipping", job_id)
                    return
                
                # Mark as running
                parse_job_service.mark_running(db, job)
                
                # Route to appropriate handler based on job_type
                if job_type == "ocr.completed":
                    _process_ocr_completed(db, job, payload, parent_job_id)
                elif job_type == "recipe.import.url.requested":
                    _process_url_job(db, job)
                elif job_type == "recipe.create.manual.requested":
                    # Manual entry - not implemented yet
                    parse_job_service.mark_error(db, job, "not_implemented", "Manual entry not yet implemented")
                elif job_type == "ingestion":
                    _process_ingestion_job(db, job, payload)
                elif job_type == "meal_plan_generate":
                    _process_meal_plan_job(db, job, payload)
                else:
                    logger.error("Unknown job type in envelope: %s", job_type)
                    parse_job_service.mark_error(db, job, "unknown_job_type", f"Unknown job type: {job_type}")
        else:
            # Legacy format (backwards compatibility)
            job_id = envelope["job_id"]
            job_type = envelope["job_type"]
            job_data = envelope.get("data", {})
            
            logger.info("Processing job %s (%s) from legacy format", job_id, job_type)
            
            with SessionLocal() as db:
                job = db.get(models.RecipeParseJob, job_id)
                if not job:
                    logger.error("Job %s not found in database", job_id)
                    return
                
                if job.status == parse_job_service.RecipeParseJobStatus.CANCELED.value:
                    logger.info("Job %s was canceled, skipping", job_id)
                    return
                
                parse_job_service.mark_running(db, job)
                
                # Route to legacy handlers
                if job_type == "image":
                    # Image jobs should now come via OCR completion, but handle legacy for migration
                    logger.warning("Legacy image job detected - should use OCR queue")
                    _process_image_job(db, job)
                elif job_type == "ingestion":
                    _process_ingestion_job(db, job, job_data)
                elif job_type == "meal_plan_generate":
                    _process_meal_plan_job(db, job, job_data)
                elif job_type == "url":
                    _process_url_job(db, job)
                else:
                    logger.error("Unknown job type: %s", job_type)
                    parse_job_service.mark_error(db, job, "unknown_job_type", f"Unknown job type: {job_type}")
    
    except Exception as exc:
        logger.exception("Failed to process job: %s", exc)
        # Try to mark job as error if we have a job_id
        try:
            payload = json.loads(payload_json)
            job_id = payload.get("job_id")
            if job_id:
                with SessionLocal() as db:
                    job = db.get(models.RecipeParseJob, job_id)
                    if job:
                        parse_job_service.mark_error(db, job, "worker_error", str(exc))
        except Exception:
            logger.exception("Failed to mark job as error")


def _process_ocr_completed(db: Session, job: Any, payload: Dict[str, Any], parent_job_id: Optional[str]) -> None:
    """
    Process an OCR completion event from the OCR service.
    
    This handler receives OCR results from the OCR service and continues the recipe
    extraction pipeline (quality check, text structuring, draft creation).
    
    The payload contains a `results[]` array with one entry per image, aligned by index.
    """
    try:
        logger.info("Processing OCR completion for job %s", job.id)
        logger.debug("Payload keys: %s", list(payload.keys()))
        
        # Extract OCR results from payload
        status = payload.get("status")
        results = payload.get("results", [])
        error = payload.get("error")
        
        logger.info("OCR completion status: %s, results count: %d, error: %s", status, len(results) if results else 0, error)
        
        # Check if there's an actual error (error dict exists AND has a code or message)
        has_error = error and (error.get("code") or error.get("message"))
        
        if status != "success" or has_error:
            error_code = error.get("code", "ocr_failed") if error else "ocr_failed"
            error_message = error.get("message", "OCR extraction failed") if error else "OCR extraction failed"
            logger.warning("OCR completion failed for job %s: %s (%s)", job.id, error_message, error_code)
            parse_job_service.mark_error(db, job, error_code, error_message)
            return
        
        if not results:
            logger.warning("OCR completion returned no results for job %s", job.id)
            parse_job_service.mark_error(db, job, "ocr_no_results", "OCR service returned no results")
            return
        
        # Sort results by index to preserve image order
        sorted_results = sorted(results, key=lambda r: r.get("index", 0))
        
        # Check for per-image errors and collect successful results
        successful_results = []
        failed_results = []
        for result in sorted_results:
            result_error = result.get("error")
            if result_error and result_error.get("code"):
                # Per-image failure
                failed_results.append({
                    "index": result.get("index", -1),
                    "error": result_error,
                })
                logger.warning(
                    "OCR failed for image index %d in job %s: %s (%s)",
                    result.get("index", -1),
                    job.id,
                    result_error.get("message", "Unknown error"),
                    result_error.get("code", "unknown"),
                )
            else:
                # Success (error is null or empty)
                successful_results.append(result)
        
        # If all images failed, mark job as failed
        if not successful_results:
            error_messages = [f"Image {r['index']}: {r['error'].get('message', 'Unknown error')}" for r in failed_results]
            error_code = failed_results[0]["error"].get("code", "ocr_all_images_failed") if failed_results else "ocr_all_images_failed"
            error_message = f"All images failed OCR: {'; '.join(error_messages)}"
            parse_job_service.mark_error(db, job, error_code, error_message)
            return
        
        # Log partial failures if any
        if failed_results:
            logger.warning(
                "Partial OCR failure for job %s: %d/%d images succeeded",
                job.id,
                len(successful_results),
                len(results),
            )
        
        # Combine OCR text from successful images only (preserving order)
        ocr_texts = []
        all_metas = []
        for result in successful_results:
            ocr_text = result.get("ocr_text", "")
            if ocr_text:
                ocr_texts.append(ocr_text)
            all_metas.append(result.get("meta", {}))
        
        # Check if job was canceled before continuing
        db.refresh(job)
        if parse_job_service.is_canceled(job):
            logger.info("Job %s was canceled during OCR processing, aborting", job.id)
            return
        
        # Combine all OCR text with double newline separator
        combined_text = "\n\n".join(ocr_texts)
        
        if not combined_text:
            error_message = "No OCR text extracted from any successful image"
            if failed_results:
                error_message += f" ({len(failed_results)} image(s) failed)"
            parse_job_service.mark_error(db, job, "ocr_no_text", error_message)
            return
        
        # Calculate mean confidence across successful images only
        confidences = [meta.get("confidence", 0.0) for meta in all_metas if meta.get("confidence") is not None]
        mean_confidence = sum(confidences) / len(confidences) if confidences else None
        
        # Get provider/tier info (use first successful result's tier for logging)
        tier = all_metas[0].get("tier", "unknown") if all_metas else "unknown"
        
        # Get ingestion from job data
        job_data = job.job_data or {}
        logger.debug("Job data for job %s: %s", job.id, job_data)
        ingestion_id = job_data.get("ingestion_id")
        if not ingestion_id:
            logger.error("Missing ingestion_id in job data for job %s. Job data: %s", job.id, job_data)
            parse_job_service.mark_error(db, job, "invalid_job_data", "Missing ingestion_id in job data")
            return
        
        logger.info("Looking up ingestion %s for job %s", ingestion_id, job.id)
        ingestion = db.get(models.RecipeIngestion, ingestion_id)
        if not ingestion:
            logger.error("Ingestion %s not found for job %s", ingestion_id, job.id)
            parse_job_service.mark_error(db, job, "invalid_ingestion", "Ingestion not found")
            return
        logger.info("Found ingestion %s for job %s", ingestion_id, job.id)
        
        ingestion.status = "RUNNING"
        db.commit()
        db.refresh(ingestion)
        
        # Quality check on combined text
        logger.info("Running quality check for job %s: text_length=%d, mean_confidence=%s", 
                   job.id, len(combined_text), mean_confidence)
        quality_result = ocr_quality.score_quality(combined_text, mean_confidence)
        logger.info("Quality check result for job %s: pass_gate=%s, hard_fail=%s, char_count=%d, gibberish=%s",
                   job.id, quality_result.get("pass_gate"), quality_result.get("hard_fail"), 
                   quality_result.get("char_count"), quality_result.get("gibberish"))
        
        if quality_result["hard_fail"] or not quality_result["pass_gate"]:
            error_code = "quality_gate_failed"
            error_message = f"OCR quality insufficient: char_count={quality_result['char_count']}, gibberish={quality_result['gibberish']}"
            logger.warning("Quality gate failed for job %s: %s", job.id, error_message)
            parse_job_service.mark_error(db, job, error_code, error_message)
            ingestion.status = "FAILED"
            ingestion.pipeline_json = {
                "attempts": [{
                    "tier": 1,
                    "status": "failed_quality",
                    "metrics": quality_result,
                }],
                "error": error_message,
            }
            db.commit()
            return
        
        # Check if job was canceled before LLM call
        db.refresh(job)
        if parse_job_service.is_canceled(job):
            logger.info("Job %s was canceled before LLM call, aborting", job.id)
            return
        
        # Text structuring (LLM call to extract recipe from combined OCR text)
        settings = get_settings()
        tier_max = job_data.get("tier_max") or ingestion.tier_max or 3
        
        logger.info("Calling LLM text structuring for job %s with model %s (text_length=%d)", 
                   job.id, settings.llm_lightweight_model_name, len(combined_text))
        try:
            draft = asyncio.run(call_text_structuring(combined_text, settings.llm_lightweight_model_name))
            logger.info("LLM text structuring completed for job %s: draft=%s", job.id, "present" if draft else "None")
            
            if draft:
                # Validate draft minimums first
                validation_passed = False
                validation_error = None
                try:
                    draft.validate_minimums()
                    validation_passed = True
                except Exception as validation_exc:
                    validation_error = str(validation_exc)
                    logger.warning("Initial draft validation failed for job %s: %s", job.id, validation_error)
                
                # Conditionally clean and validate draft using lightweight model if validation failed
                # or if we detect common issues (units in ingredient names, combined ingredients, missing description)
                needs_cleaning = False
                if not validation_passed:
                    needs_cleaning = True
                    logger.info("Draft validation failed, will attempt cleanup for job %s", job.id)
                else:
                    # Check for common issues that rule-based cleanup might miss
                    for ing in draft.ingredients:
                        # Check if unit might be in name (common units in name)
                        name_lower = ing.name.lower() if ing.name else ""
                        common_units = ["cup", "cups", "tsp", "teaspoon", "tbsp", "tablespoon", "oz", "ounce", "lb", "pound", "g", "gram", "kg", "ml", "liter", "clove", "cloves"]
                        if any(unit in name_lower for unit in common_units) and not ing.unit:
                            needs_cleaning = True
                            logger.info("Detected unit in ingredient name, will cleanup for job %s", job.id)
                            break
                        # Check for combined ingredients (and, comma-separated)
                        if " and " in name_lower or ("," in name_lower and len(name_lower.split(",")) > 1):
                            needs_cleaning = True
                            logger.info("Detected combined ingredients, will cleanup for job %s", job.id)
                            break
                    if not draft.description:
                        # Missing description - will be added during cleanup
                        needs_cleaning = True
                        logger.info("Missing description, will add during cleanup for job %s", job.id)
                
                if needs_cleaning:
                    # Check if job was canceled before cleaning
                    db.refresh(job)
                    if parse_job_service.is_canceled(job):
                        logger.info("Job %s was canceled before draft cleaning, aborting", job.id)
                        return
                    
                    logger.info("Cleaning and validating draft for job %s with lightweight model", job.id)
                    try:
                        draft = asyncio.run(clean_and_validate_draft(draft, settings.llm_lightweight_model_name))
                        logger.info("Draft cleaning completed for job %s", job.id)
                    except Exception as cleanup_exc:
                        logger.warning("Draft cleaning failed for job %s: %s, using original draft", job.id, cleanup_exc)
                        # Continue with original draft
                
                # Validate draft minimums again (after cleanup if it was done)
                try:
                    draft.validate_minimums()
                except Exception as validation_exc:
                    logger.warning("Draft validation failed for job %s: %s", job.id, validation_exc)
                    parse_job_service.mark_error(db, job, "draft_validation_failed", str(validation_exc))
                    ingestion.status = "FAILED"
                    ingestion.pipeline_json = {
                        "attempts": [{
                            "tier": 1,
                            "status": "failed_validation",
                            "metrics": quality_result,
                            "error": str(validation_exc),
                        }],
                    }
                    db.commit()
                    return
                
                # Success - create recipe draft
                from jarvis_recipes.app.services import mailbox_service
                
                ingestion.status = "SUCCEEDED"
                ingestion.pipeline_json = {
                    "attempts": [{
                        "tier": 1,
                        "status": "success",
                        "metrics": quality_result,
                        "provider": tier,  # Use tier from first successful result
                        "image_count": len(results),
                        "successful_images": len(successful_results),
                        "failed_images": len(failed_results),
                        "per_image_errors": [
                            {"index": r["index"], "code": r["error"].get("code"), "message": r["error"].get("message")}
                            for r in failed_results
                        ] if failed_results else None,
                    }],
                    "selected_tier": 1,
                }
                db.commit()
                db.refresh(ingestion)
                
                # Publish completion message
                mailbox_service.publish(
                    db,
                    ingestion.user_id,
                    "recipe_image_ingestion_completed",
                    {
                        "ingestion_id": ingestion.id,
                        "recipe_draft": draft.model_dump(),
                        "pipeline": ingestion.pipeline_json,
                    },
                )
                
                # Mark job as complete
                # Store the draft in result_json (ParseResult expects ParsedRecipe, but we have RecipeDraft)
                job.result_json = {
                    "recipe_draft": draft.model_dump(),
                    "pipeline": ingestion.pipeline_json,
                }
                job.status = parse_job_service.RecipeParseJobStatus.COMPLETE.value
                db.commit()
                db.refresh(job)
                logger.info("OCR completion processed successfully for job %s", job.id)
            else:
                parse_job_service.mark_error(db, job, "text_structuring_failed", "LLM failed to extract recipe from OCR text")
                ingestion.status = "FAILED"
                ingestion.pipeline_json = {
                    "attempts": [{
                        "tier": 1,
                        "status": "failed_text_structuring",
                        "metrics": quality_result,
                    }],
                }
                db.commit()
        except Exception as exc:
            logger.exception("Text structuring failed for job %s: %s", job.id, exc)
            parse_job_service.mark_error(db, job, "text_structuring_exception", str(exc))
            ingestion.status = "FAILED"
            ingestion.pipeline_json = {
                "attempts": [{
                    "tier": 1,
                    "status": "failed_text_structuring",
                    "error": str(exc),
                }],
            }
            db.commit()
    
    except Exception as exc:
        logger.exception("OCR completion processing failed for job %s: %s", job.id, exc)
        try:
            parse_job_service.mark_error(db, job, "ocr_completion_error", str(exc))
        except Exception as exc2:
            logger.exception("Failed to mark job %s as error: %s", job.id, exc2)


def _process_image_job(db: Session, job: Any) -> None:
    """
    Legacy handler for image jobs (deprecated).
    
    Image jobs should now come via OCR completion events. This handler is kept
    for backwards compatibility during migration.
    """
    try:
        logger.warning("Legacy image job handler called for job %s - should use OCR queue", job.id)
        logger.debug("Starting image job processing for job %s", job.id)
        asyncio.run(process_image_ingestion_job(db, job))
        logger.debug("Completed image job processing for job %s", job.id)
    except Exception as exc:
        logger.exception("Image job %s crashed with exception: %s", job.id, exc)
        try:
            parse_job_service.mark_error(db, job, "worker_error", str(exc))
        except Exception as exc2:
            logger.exception("Failed to mark job %s as error after exception: %s", job.id, exc2)


def _process_ingestion_job(db: Session, job: Any, job_data: Dict[str, Any]) -> None:
    """Process an ingestion job."""
    settings = get_settings()
    max_retries = settings.llm_recipe_queue_max_retries
    
    try:
        input_payload = IngestionInput.model_validate(job_data)
    except Exception as exc:
        parse_job_service.mark_error(db, job, "invalid_payload", str(exc))
        return
    
    try:
        result = asyncio.run(parse_recipe_ingestion(input_payload))
        if result.success:
            parse_job_service.mark_complete(db, job, result)
            logger.info("Job %s complete", job.id)
        else:
            # Don't retry encoding errors - they won't fix themselves
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
                # Re-enqueue to Redis
                from jarvis_recipes.app.services.queue_service import enqueue_job
                enqueue_job(job.job_type, job.id, job.job_data or {})
            else:
                # If result has next_action, store the full result so client can see the suggestion
                if result.next_action:
                    parse_job_service.mark_complete(db, job, result)
                    job.status = parse_job_service.RecipeParseJobStatus.ERROR.value
                    job.error_code = result.error_code or "parse_failed"
                    job.error_message = result.error_message or "Parse failed"
                    db.commit()
                    logger.warning("Job %s failed but stored next_action=%s: %s", job.id, result.next_action, result.error_message)
                else:
                    parse_job_service.mark_error(db, job, result.error_code or "parse_failed", result.error_message or "Parse failed")
                    logger.warning("Job %s failed: %s", job.id, result.error_message)
    except Exception as exc:
        logger.exception("Job %s crashed", job.id)
        parse_job_service.mark_error(db, job, "worker_error", str(exc))


def _process_meal_plan_job(db: Session, job: Any, job_data: Dict[str, Any]) -> None:
    """Process a meal plan generation job."""
    try:
        request_id = job_data.get("request_id") or str(uuid.uuid4())
        payload = job_data.get("payload") or {}
        req = MealPlanGenerateRequest.model_validate(payload)
    except Exception as exc:
        parse_job_service.mark_error(db, job, "invalid_payload", str(exc))
        return
    
    try:
        result, slot_failures = meal_plan_service.generate_meal_plan(db, job.user_id, req, request_id)
        meal_plan_service.publish_completed(db, job.user_id, request_id, result, slot_failures)
        job.result_json = {"result": result.model_dump(mode="json"), "slot_failures_count": slot_failures}
        job.status = parse_job_service.RecipeParseJobStatus.COMPLETE.value
        db.commit()
        db.refresh(job)
    except Exception as exc:
        request_id = job_data.get("request_id") or str(uuid.uuid4())
        meal_plan_service.publish_failed(db, job.user_id, request_id, "generation_failed", str(exc))
        parse_job_service.mark_error(db, job, "generation_failed", str(exc))


def _process_url_job(db: Session, job: Any) -> None:
    """Process a URL parsing job."""
    settings = get_settings()
    max_retries = settings.llm_recipe_queue_max_retries
    
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
                # Re-enqueue to Redis
                from jarvis_recipes.app.services.queue_service import enqueue_job
                enqueue_job(job.job_type, job.id, job.job_data or {})
            else:
                parse_job_service.mark_error(db, job, result.error_code or "parse_failed", result.error_message or "Parse failed")
                logger.warning("Job %s failed: %s", job.id, result.error_message)
    except Exception as exc:
        logger.exception("Job %s crashed", job.id)
        parse_job_service.mark_error(db, job, "worker_error", str(exc))

