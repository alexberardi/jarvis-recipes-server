"""
Worker functions for processing jobs from Redis queue.

This module contains the actual job processing functions that are called
by RQ workers. These functions handle the business logic for each job type.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from jarvis_recipes.app.db.session import SessionLocal
from jarvis_recipes.app.schemas.ingestion_input import IngestionInput
from jarvis_recipes.app.schemas.meal_plan import MealPlanGenerateRequest
from jarvis_recipes.app.db import models
from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.services import mailbox_service, meal_plan_service, ocr_join, parse_job_service, url_recipe_parser
from jarvis_recipes.app.services.image_ingest_worker import process_image_ingestion_job
from jarvis_recipes.app.services.ingestion_service import parse_recipe as parse_recipe_ingestion
from jarvis_recipes.app.services import ocr_quality
from jarvis_recipes.app.services import llm_client
from jarvis_recipes.app.services.llm_client import call_text_structuring, clean_and_validate_draft
from jarvis_recipes.app.services import queue_service
from jarvis_recipes.app.services.queue_service import enqueue_job
from jarvis_recipes.app.services.settings_service import get_settings_service

logger = logging.getLogger(__name__)


def process_job(payload_json: str) -> None:
    """
    Process a job from the Redis queue.
    
    This is the main entry point for RQ workers. It handles both:
    - New envelope format (per PRD queue-flow.md)
    - Legacy format (for backwards compatibility)
    
    All exceptions are caught and handled gracefully to prevent worker crashes.
    
    Args:
        payload_json: JSON string containing either envelope or legacy format
    """
    db = None
    job_id = None
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
            
            db = SessionLocal()
            try:
                # For ocr.completed events, use workflow_id or parent_job_id to find the original job
                # (job_id is a new UUID created by OCR service)
                if job_type in ("ocr.completed", "ocr.join_deadline"):
                    # Both carry a job_id of their own -- OCR mints a fresh uuid
                    # for a completion, and the deadline timer uses a derived id
                    # so repeated scheduling collapses to one. Neither names a
                    # RecipeParseJob; the workflow does.
                    lookup_id = workflow_id or parent_job_id or job_id
                    logger.debug("%s: looking up job by workflow_id/parent_job_id: %s", job_type, lookup_id)
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
                try:
                    parse_job_service.mark_running(db, job)
                except Exception as exc:
                    logger.exception("Failed to mark job %s as running: %s", job_id, exc)
                    db.rollback()
                    # Continue anyway - job might already be running
                
                # Route to appropriate handler based on job_type
                try:
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
                    elif job_type == "grocery_match":
                        _process_grocery_match_job(db, job, payload)
                    elif job_type == "ocr.join_deadline":
                        _process_ocr_join_deadline(db, job, payload, workflow_id)
                    else:
                        logger.error("Unknown job type in envelope: %s", job_type)
                        parse_job_service.mark_error(db, job, "unknown_job_type", f"Unknown job type: {job_type}")
                except Exception as handler_exc:
                    logger.exception("Handler failed for job %s (type %s): %s", job_id, job_type, handler_exc)
                    try:
                        db.rollback()
                        parse_job_service.mark_error(db, job, "handler_error", str(handler_exc))
                    except Exception as mark_exc:
                        logger.exception("Failed to mark job %s as error after handler failure: %s", job_id, mark_exc)
            finally:
                if db:
                    try:
                        db.close()
                    except Exception as close_exc:
                        logger.exception("Error closing database session for job %s: %s", job_id, close_exc)
        else:
            # Legacy format (backwards compatibility)
            job_id = envelope.get("job_id")
            job_type = envelope.get("job_type")
            job_data = envelope.get("data", {})
            
            logger.info("Processing job %s (%s) from legacy format", job_id, job_type)
            
            db = SessionLocal()
            try:
                if not job_id:
                    logger.error("Legacy job missing job_id in envelope")
                    return
                
                job = db.get(models.RecipeParseJob, job_id)
                if not job:
                    logger.error("Job %s not found in database", job_id)
                    return
                
                if job.status == parse_job_service.RecipeParseJobStatus.CANCELED.value:
                    logger.info("Job %s was canceled, skipping", job_id)
                    return
                
                try:
                    parse_job_service.mark_running(db, job)
                except Exception as exc:
                    logger.exception("Failed to mark job %s as running: %s", job_id, exc)
                    db.rollback()
                
                # Route to legacy handlers
                try:
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
                    elif job_type == "grocery_match":
                        _process_grocery_match_job(db, job, job_data)
                    else:
                        logger.error("Unknown job type: %s", job_type)
                        parse_job_service.mark_error(db, job, "unknown_job_type", f"Unknown job type: {job_type}")
                except Exception as handler_exc:
                    logger.exception("Handler failed for job %s (type %s): %s", job_id, job_type, handler_exc)
                    try:
                        db.rollback()
                        parse_job_service.mark_error(db, job, "handler_error", str(handler_exc))
                    except Exception as mark_exc:
                        logger.exception("Failed to mark job %s as error after handler failure: %s", job_id, mark_exc)
            finally:
                if db:
                    try:
                        db.close()
                    except Exception as close_exc:
                        logger.exception("Error closing database session for job %s: %s", job_id, close_exc)
    
    except json.JSONDecodeError as exc:
        logger.exception("Failed to parse job payload JSON: %s", exc)
        # Can't recover from JSON decode errors - log and continue
    except Exception as exc:
        logger.exception("Unexpected error processing job: %s", exc)
        # Try to mark job as error if we have a job_id
        if job_id:
            try:
                db = SessionLocal()
                try:
                    job = db.get(models.RecipeParseJob, job_id)
                    if job:
                        parse_job_service.mark_error(db, job, "worker_error", str(exc))
                except Exception as mark_exc:
                    logger.exception("Failed to mark job %s as error: %s", job_id, mark_exc)
                finally:
                    if db:
                        try:
                            db.close()
                        except Exception as e:
                            pass
            except Exception as db_exc:
                logger.exception("Failed to create database session to mark job %s as error: %s", job_id, db_exc)


def _ingestion_for(db: Session, job: Any) -> Optional[Any]:
    """The RecipeIngestion this job is extracting into."""
    ingestion_id = (job.job_data or {}).get("ingestion_id")
    if not ingestion_id:
        logger.error("Missing ingestion_id in job data for job %s", job.id)
        return None
    return db.get(models.RecipeIngestion, ingestion_id)


def _process_ocr_join_deadline(
    db: Session, job: Any, payload: Dict[str, Any], workflow_id: Optional[str]
) -> None:
    """Release a join that is still waiting for hosts that have not answered.

    Fires once per workflow, `ocr_join_timeout_seconds` after the first reading
    arrived. Usually a no-op: if every host answered, the last one already
    claimed the join and continued.
    """
    ingestion = _ingestion_for(db, job)
    if ingestion is None:
        return

    if not (ingestion.ocr_readings or []):
        # Nothing came back at all. Not this handler's problem to report -- the
        # job is still PENDING and the stale-job reaper owns that case -- but
        # worth saying so, because it means every OCR host is down.
        logger.warning("OCR join deadline for %s: no host answered", workflow_id)
        return

    if not ocr_join.claim(db, ingestion.id):
        logger.debug("OCR join deadline for %s: already released", workflow_id)
        return

    logger.info(
        "OCR join deadline for %s: continuing with %s", workflow_id, ocr_join.describe(ingestion)
    )
    _structure_from_readings(db, job, ingestion)


def _process_ocr_completed(db: Session, job: Any, payload: Dict[str, Any], parent_job_id: Optional[str]) -> None:
    """
    Process an OCR completion event from the OCR service.
    
    This handler receives OCR results from the OCR service and continues the recipe
    extraction pipeline (quality check, text structuring, draft creation).
    
    The payload contains a `results[]` array with one entry per image, aligned by index.
    
    All exceptions are caught and handled gracefully to prevent worker crashes.
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
        
        # One host has answered. Record it and see whether to wait for the others.
        job_data = job.job_data or {}
        ingestion_id = job_data.get("ingestion_id")
        if not ingestion_id:
            logger.error("Missing ingestion_id in job data for job %s. Job data: %s", job.id, job_data)
            parse_job_service.mark_error(db, job, "invalid_job_data", "Missing ingestion_id in job data")
            return

        ingestion = db.get(models.RecipeIngestion, ingestion_id)
        if not ingestion:
            logger.error("Ingestion %s not found for job %s", ingestion_id, job.id)
            parse_job_service.mark_error(db, job, "invalid_ingestion", "Ingestion not found")
            return

        count = ocr_join.record_reading(db, ingestion, sorted_results)
        expected = ingestion.ocr_expected or 1
        logger.info(
            "OCR reading %d/%d for job %s from %s", count, expected, job.id, ocr_join.describe(ingestion)
        )

        if not ocr_join.is_complete(ingestion):
            # Hold for the slower hosts. The deadline timer releases the join if
            # they never answer -- scheduled on every arrival, under one
            # deterministic id, so it exists even if the first arrival raced it.
            queue_service.schedule_join_deadline(job.id, ingestion.id)
            return

        if not ocr_join.claim(db, ingestion.id):
            # The deadline fired first and already continued. Our reading is
            # recorded either way; it simply arrived too late to be used.
            logger.info("OCR join for job %s already released; discarding late reading", job.id)
            return

        _structure_from_readings(db, job, ingestion)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unhandled error in OCR completion for job %s: %s", job.id, exc)
        try:
            db.rollback()
            parse_job_service.mark_error(db, job, "ocr_completion_error", str(exc))
        except Exception:  # noqa: BLE001
            logger.exception("Could not mark job %s failed", job.id)


def _structure_from_readings(db: Session, job: Any, ingestion: Any) -> None:
    """Continue the pipeline once the OCR readings are in.

    Reached from the last arriving reading or from the deadline timer, never
    both -- ocr_join.claim decides.
    """
    try:
        job_data = job.job_data or {}
        readings = ocr_join.readings_for_llm(ingestion)

        # (provider, text) per host, empty readings dropped: a host that answered
        # with nothing should not become a blank block in the prompt.
        texts = [(r.get("provider") or "", ocr_join.combine(r.get("results") or [])) for r in readings]
        texts = [(provider, text) for provider, text in texts if text.strip()]

        if not texts:
            parse_job_service.mark_error(
                db, job, "ocr_no_text", "No OCR text extracted from any image"
            )
            ingestion.status = "FAILED"
            db.commit()
            return

        all_metas = [
            result.get("meta", {})
            for reading in readings
            for result in (reading.get("results") or [])
        ]
        confidences = [m.get("confidence") for m in all_metas if m.get("confidence") is not None]
        mean_confidence = sum(confidences) / len(confidences) if confidences else None
        tier = texts[0][0] or "unknown"

        # The gate judges the BEST reading, not the concatenation. Asking "is any
        # engine's reading good enough to be worth the LLM?" is the real question;
        # concatenating lets one engine's mush drag a good reading below the line.
        combined_text = max((t for _, t in texts), key=lambda t: len(t.split()))

        ingestion.status = "RUNNING"
        try:
            db.commit()
            db.refresh(ingestion)
        except Exception as commit_exc:
            logger.exception("Failed to commit ingestion status update for job %s: %s", job.id, commit_exc)
            db.rollback()
            parse_job_service.mark_error(db, job, "database_error", f"Failed to update ingestion status: {commit_exc}")
            return
        
        # Quality check on combined text
        logger.info("Running quality check for job %s: text_length=%d, mean_confidence=%s", 
                   job.id, len(combined_text), mean_confidence)
        quality_result = ocr_quality.score_quality(combined_text, mean_confidence)
        logger.info("Quality check result for job %s: pass_gate=%s, hard_fail=%s, char_count=%d, gibberish=%s",
                   job.id, quality_result.get("pass_gate"), quality_result.get("hard_fail"), 
                   quality_result.get("char_count"), quality_result.get("gibberish"))
        
        if quality_result["hard_fail"] or not quality_result["pass_gate"]:
            error_code = "quality_gate_failed"
            # What the PERSON sees. "char_count=256, gibberish=True" is a true
            # description of the measurement and useless to someone holding a
            # phone: it reads as a bug in the app rather than a limit of the
            # engines. The commonest cause by far is handwriting -- the installed
            # providers are printed-text OCR and produce mush on cursive -- and
            # the useful next step is typing it in, which the app already offers.
            # The metrics stay in the log and in pipeline_json for debugging.
            error_message = (
                "Couldn't read enough text from this photo. Handwritten recipes, "
                "angled shots and low light are the usual culprits — try a "
                "straight-on photo in good light, or enter it by hand."
            )
            logger.warning(
                "Quality gate failed for job %s: char_count=%s line_count=%s "
                "token_count=%s gibberish=%s alpha_ratio=%.2f",
                job.id,
                quality_result.get("char_count"),
                quality_result.get("line_count"),
                quality_result.get("token_count"),
                quality_result.get("gibberish"),
                quality_result.get("alpha_ratio", 0.0),
            )
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
        lightweight_model = get_settings_service().get_str("llm.lightweight_model_name", "live")
        tier_max = job_data.get("tier_max") or ingestion.tier_max or 3
        
        logger.info(
            "Calling LLM text structuring for job %s with model %s (%d reading(s): %s)",
            job.id,
            lightweight_model,
            len(texts),
            ", ".join(p or "?" for p, _ in texts),
        )
        try:
            draft = asyncio.run(
                call_text_structuring(combined_text, lightweight_model, readings=texts)
            )
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
                        draft = asyncio.run(clean_and_validate_draft(draft, lightweight_model))
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
                ingestion.status = "SUCCEEDED"
                # One attempt per ENGINE that answered, rather than the single
                # entry this used to write. With a fan-out there is no "the"
                # provider any more, and which engines were reconciled is the
                # first thing anyone debugging a wrong import wants to know.
                ingestion.pipeline_json = {
                    "attempts": [
                        {
                            "tier": 1,
                            "status": "success",
                            "provider": reading.get("provider"),
                            "image_count": len(reading.get("results") or []),
                            "failed_images": sum(
                                1 for r in (reading.get("results") or []) if r.get("error")
                            ),
                            "per_image_errors": [
                                {
                                    "index": r.get("index"),
                                    "code": (r.get("error") or {}).get("code"),
                                    "message": (r.get("error") or {}).get("message"),
                                }
                                for r in (reading.get("results") or [])
                                if r.get("error")
                            ]
                            or None,
                        }
                        for reading in readings
                    ],
                    "metrics": quality_result,
                    "providers_used": [p for p, _ in texts],
                    "selected_tier": 1,
                }
                try:
                    db.commit()
                    db.refresh(ingestion)
                except Exception as commit_exc:
                    logger.exception("Failed to commit ingestion success for job %s: %s", job.id, commit_exc)
                    db.rollback()
                    parse_job_service.mark_error(db, job, "database_error", f"Failed to save ingestion result: {commit_exc}")
                    return
                
                # Publish completion message (non-critical - log but don't fail job if this fails)
                try:
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
                except Exception as publish_exc:
                    logger.warning("Failed to publish completion message for job %s: %s", job.id, publish_exc)
                    # Continue - job completion is more important than messaging
                
                # Mark job as complete
                try:
                    # Store the draft in result_json (ParseResult expects ParsedRecipe, but we have RecipeDraft)
                    job.result_json = {
                        "recipe_draft": draft.model_dump(),
                        "pipeline": ingestion.pipeline_json,
                    }
                    job.status = parse_job_service.RecipeParseJobStatus.COMPLETE.value
                    db.commit()
                    db.refresh(job)
                    logger.info("OCR completion processed successfully for job %s", job.id)
                except Exception as commit_exc:
                    logger.exception("Failed to mark job %s as complete: %s", job.id, commit_exc)
                    db.rollback()
                    # Try one more time with just error marking
                    try:
                        parse_job_service.mark_error(db, job, "database_error", f"Failed to save job completion: {commit_exc}")
                    except (OSError, RuntimeError):
                        logger.exception("Failed to mark job %s as error after completion failure", job.id)
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
            db.rollback()  # Rollback any partial changes
            parse_job_service.mark_error(db, job, "ocr_completion_error", str(exc))
        except Exception as exc2:
            logger.exception("Failed to mark job %s as error: %s", job.id, exc2)
            # Last resort - try to rollback
            try:
                db.rollback()
            except (OSError, RuntimeError):
                pass


def _process_image_job(db: Session, job: Any) -> None:
    """
    Legacy handler for image jobs (deprecated).
    
    Image jobs should now come via OCR completion events. This handler is kept
    for backwards compatibility during migration.
    All exceptions are caught and handled gracefully.
    """
    try:
        logger.warning("Legacy image job handler called for job %s - should use OCR queue", job.id)
        logger.debug("Starting image job processing for job %s", job.id)
        asyncio.run(process_image_ingestion_job(db, job))
        logger.debug("Completed image job processing for job %s", job.id)
    except Exception as exc:
        logger.exception("Image job %s crashed with exception: %s", job.id, exc)
        try:
            db.rollback()
            parse_job_service.mark_error(db, job, "worker_error", str(exc))
        except (OSError, RuntimeError) as exc2:
            logger.exception("Failed to mark job %s as error after exception: %s", job.id, exc2)
            # Last resort rollback
            try:
                db.rollback()
            except (OSError, RuntimeError):
                pass


def _process_ingestion_job(db: Session, job: Any, job_data: Dict[str, Any]) -> None:
    """Process an ingestion job. All exceptions are caught and handled gracefully."""
    max_retries = get_settings_service().get_int("queue.max_retries", 3)
    
    try:
        input_payload = IngestionInput.model_validate(job_data)
    except Exception as exc:
        logger.exception("Invalid payload for job %s: %s", job.id, exc)
        try:
            parse_job_service.mark_error(db, job, "invalid_payload", str(exc))
        except Exception as mark_exc:
            logger.exception("Failed to mark job %s as error for invalid payload: %s", job.id, mark_exc)
            db.rollback()
        return
    
    try:
        result = asyncio.run(parse_recipe_ingestion(input_payload))
        if result.success:
            try:
                parse_job_service.mark_complete(db, job, result)
                logger.info("Job %s complete", job.id)
            except Exception as commit_exc:
                logger.exception("Failed to mark job %s as complete: %s", job.id, commit_exc)
                db.rollback()
                parse_job_service.mark_error(db, job, "database_error", f"Failed to save result: {commit_exc}")
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
                try:
                    job.status = parse_job_service.RecipeParseJobStatus.PENDING.value
                    job.error_code = result.error_code
                    job.error_message = result.error_message
                    db.commit()
                    db.refresh(job)
                    # Re-enqueue to Redis
                    enqueue_job(job.job_type, job.id, job.job_data or {})
                except Exception as retry_exc:
                    logger.exception("Failed to retry job %s: %s", job.id, retry_exc)
                    db.rollback()
                    parse_job_service.mark_error(db, job, result.error_code or "parse_failed", result.error_message or "Parse failed")
            else:
                # If result has next_action, store the full result so client can see the suggestion
                if result.next_action:
                    try:
                        parse_job_service.mark_complete(db, job, result)
                        job.status = parse_job_service.RecipeParseJobStatus.ERROR.value
                        job.error_code = result.error_code or "parse_failed"
                        job.error_message = result.error_message or "Parse failed"
                        db.commit()
                        logger.warning("Job %s failed but stored next_action=%s: %s", job.id, result.next_action, result.error_message)
                    except Exception as commit_exc:
                        logger.exception("Failed to store next_action for job %s: %s", job.id, commit_exc)
                        db.rollback()
                        parse_job_service.mark_error(db, job, result.error_code or "parse_failed", result.error_message or "Parse failed")
                else:
                    try:
                        parse_job_service.mark_error(db, job, result.error_code or "parse_failed", result.error_message or "Parse failed")
                        logger.warning("Job %s failed: %s", job.id, result.error_message)
                    except Exception as error_exc:
                        logger.exception("Failed to mark job %s as error: %s", job.id, error_exc)
                        db.rollback()
    except Exception as exc:
        logger.exception("Job %s crashed: %s", job.id, exc)
        try:
            db.rollback()
            parse_job_service.mark_error(db, job, "worker_error", str(exc))
        except Exception as mark_exc:
            logger.exception("Failed to mark job %s as error after crash: %s", job.id, mark_exc)


def _process_meal_plan_job(db: Session, job: Any, job_data: Dict[str, Any]) -> None:
    """Process a meal plan generation job. All exceptions are caught and handled gracefully."""
    try:
        request_id = job_data.get("request_id") or str(uuid.uuid4())
        payload = job_data.get("payload") or {}
        req = MealPlanGenerateRequest.model_validate(payload)
    except Exception as exc:
        logger.exception("Invalid payload for meal plan job %s: %s", job.id, exc)
        try:
            parse_job_service.mark_error(db, job, "invalid_payload", str(exc))
        except Exception as mark_exc:
            logger.exception("Failed to mark job %s as error for invalid payload: %s", job.id, mark_exc)
            db.rollback()
        return
    
    try:
        # No token out here -- the request that queued this finished long ago --
        # so the caller is rebuilt from the job row, which carries the household
        # precisely so candidate search can see the family's box.
        job_user = CurrentUser(id=int(job.user_id), household_id=job.household_id)
        result, slot_failures = meal_plan_service.generate_meal_plan(db, job_user, req, request_id)
        try:
            meal_plan_service.publish_completed(db, job.user_id, request_id, result, slot_failures)
        except Exception as publish_exc:
            logger.warning("Failed to publish meal plan completion for job %s: %s", job.id, publish_exc)
            # Continue - job completion is more important than messaging
        
        try:
            job.result_json = {"result": result.model_dump(mode="json"), "slot_failures_count": slot_failures}
            job.status = parse_job_service.RecipeParseJobStatus.COMPLETE.value
            db.commit()
            db.refresh(job)
        except Exception as commit_exc:
            logger.exception("Failed to mark meal plan job %s as complete: %s", job.id, commit_exc)
            db.rollback()
            parse_job_service.mark_error(db, job, "database_error", f"Failed to save result: {commit_exc}")
    except Exception as exc:
        logger.exception("Meal plan job %s crashed: %s", job.id, exc)
        try:
            db.rollback()
            request_id = job_data.get("request_id") or str(uuid.uuid4())
            try:
                meal_plan_service.publish_failed(db, job.user_id, request_id, "generation_failed", str(exc))
            except Exception as publish_exc:
                logger.warning("Failed to publish meal plan failure for job %s: %s", job.id, publish_exc)
            parse_job_service.mark_error(db, job, "generation_failed", str(exc))
        except Exception as mark_exc:
            logger.exception("Failed to mark meal plan job %s as error after crash: %s", job.id, mark_exc)


def _process_url_job(db: Session, job: Any) -> None:
    """Process a URL parsing job. All exceptions are caught and handled gracefully."""
    max_retries = get_settings_service().get_int("queue.max_retries", 3)
    
    try:
        result = asyncio.run(url_recipe_parser.parse_recipe_from_url(job.url, job.use_llm_fallback))
        if result.success:
            try:
                parse_job_service.mark_complete(db, job, result)
                logger.info("Job %s complete", job.id)
            except Exception as commit_exc:
                logger.exception("Failed to mark job %s as complete: %s", job.id, commit_exc)
                db.rollback()
                parse_job_service.mark_error(db, job, "database_error", f"Failed to save result: {commit_exc}")
        else:
            should_retry = job.attempts < max_retries and (result.error_code in {"llm_timeout", "llm_failed", "fetch_failed"})
            if should_retry:
                logger.warning("Job %s failed with %s; retrying (attempt %s/%s)", job.id, result.error_code, job.attempts, max_retries)
                try:
                    job.status = parse_job_service.RecipeParseJobStatus.PENDING.value
                    job.error_code = result.error_code
                    job.error_message = result.error_message
                    db.commit()
                    db.refresh(job)
                    # Re-enqueue to Redis
                    enqueue_job(job.job_type, job.id, job.job_data or {})
                except Exception as retry_exc:
                    logger.exception("Failed to retry job %s: %s", job.id, retry_exc)
                    db.rollback()
                    parse_job_service.mark_error(db, job, result.error_code or "parse_failed", result.error_message or "Parse failed")
            else:
                try:
                    parse_job_service.mark_error(db, job, result.error_code or "parse_failed", result.error_message or "Parse failed")
                    logger.warning("Job %s failed: %s", job.id, result.error_message)
                except Exception as error_exc:
                    logger.exception("Failed to mark job %s as error: %s", job.id, error_exc)
                    db.rollback()
    except Exception as exc:
        logger.exception("Job %s crashed: %s", job.id, exc)
        try:
            db.rollback()
            parse_job_service.mark_error(db, job, "worker_error", str(exc))
        except Exception as mark_exc:
            logger.exception("Failed to mark job %s as error after crash: %s", job.id, mark_exc)


def _process_grocery_match_job(db, job, job_data):
    """Learn grocery aliases for ingredients the map had never seen.

    Nothing is waiting on this: the cart link was returned to the client the
    moment the deterministic pass finished. Its whole purpose is that the SAME
    list next week needs no picker.

    The worker holds no token, so the household is rebuilt from the job row --
    the same reason `household_id` is carried on RecipeParseJob at all. Without
    it the candidate list would be scoped to the authoring user and a housemate's
    saved products would be invisible to the pass.
    """
    from jarvis_recipes.app.services import grocery_service

    data = job_data or job.job_data or {}
    retailer = data.get("retailer", "walmart")
    unmatched = [n for n in (data.get("unmatched") or []) if isinstance(n, str)]
    if not unmatched:
        parse_job_service.mark_error(db, job, "empty_job", "No ingredients to match")
        return

    user = CurrentUser(id=int(job.user_id), household_id=job.household_id)
    candidates = grocery_service.list_map(db, user, retailer)
    if not candidates:
        # Raced with the map being emptied. Nothing to match against, and
        # inventing a SKU is the failure mode this whole design avoids.
        _mark_grocery_complete(db, job, learned=[], attempted=unmatched)
        return

    messages = grocery_service.build_match_prompt(candidates, unmatched)
    matches = asyncio.run(llm_client.match_grocery_items(messages))
    learned = grocery_service.apply_matches(db, user, matches, retailer)

    logger.info(
        "Grocery match job %s: %d/%d ingredients learned",
        job.id,
        len(learned),
        len(unmatched),
    )
    _mark_grocery_complete(db, job, learned=learned, attempted=unmatched)


def _mark_grocery_complete(db, job, learned, attempted):
    """Finish a grocery job.

    Not parse_job_service.mark_complete: that takes a ParseResult and writes a
    recipe-shaped payload. This job produces neither.
    """
    job.status = parse_job_service.RecipeParseJobStatus.COMPLETE.value
    job.result_json = {
        "learned": [
            {"ingredient_name": m.ingredient_name, "sku": m.sku, "product_name": m.product_name}
            for m in learned
        ],
        "attempted": attempted,
        # Zero learned is a legitimate outcome, not a failure: the model is meant
        # to decline when nothing in the map is the same grocery item.
        "unresolved": [
            name
            for name in attempted
            if name not in {m.ingredient_name for m in learned}
        ],
    }
    job.completed_at = datetime.utcnow()
    db.commit()
