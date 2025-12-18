import asyncio
import logging
from typing import List, Optional

from sqlalchemy.orm import Session

from jarvis_recipes.app.db import models
from jarvis_recipes.app.services import mailbox_service, parse_job_service, s3_storage
from jarvis_recipes.app.services.image_ingest_pipeline import run_ingestion_pipeline
from jarvis_recipes.app.services import llm_client
from jarvis_recipes.app.core.config import get_settings
import subprocess
import tempfile
import json
import time
import os
import sys
from typing import Any, Dict, List, Optional, Tuple
from jarvis_recipes.app.schemas.ingestion import RecipeDraft

logger = logging.getLogger(__name__)


async def _load_images_from_s3(keys: List[str]) -> List[bytes]:
    loop = asyncio.get_event_loop()
    return [await loop.run_in_executor(None, s3_storage.download_image, key) for key in keys]


async def _run_vision_subprocess(
    image_bytes: bytes,
    image_index: int,
    image_count: int,
    current_draft: Dict[str, Any],
    title_hint: Optional[str],
    settings,
) -> Tuple[Optional[dict], List[str], Dict[str, Any]]:
    tmp_dir = tempfile.mkdtemp()
    img_path = os.path.join(tmp_dir, f"img_{image_index}.jpg")
    with open(img_path, "wb") as f:
        f.write(image_bytes)
    payload = {
        "current_draft": current_draft,
        "image_index": image_index,
        "image_count": image_count,
        "is_final_image": image_index == image_count,
        "title_hint": title_hint,
    }
    cmd = [
        sys.executable,
        "-m",
        "jarvis_recipes.app.services.vision_runner",
        "--model-name",
        settings.llm_vision_model_name,
        "--timeout-seconds",
        str(settings.vision_timeout_seconds),
        "--image-path",
        img_path,
        "--payload-json",
        json.dumps(payload),
    ]
    start = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    duration_ms = int((time.time() - start) * 1000)
    stderr_snippet = (proc.stderr or "")[:1000]
    if proc.returncode != 0:
        return None, [f"subprocess failed rc={proc.returncode} stderr={stderr_snippet}"], {"duration_ms": duration_ms, "stderr": stderr_snippet}
    try:
        out = json.loads(proc.stdout)
        if "error" in out:
            return None, [f"runner error: {out['error']}"], {"duration_ms": duration_ms, "stderr": stderr_snippet}
        return out.get("draft"), out.get("warnings") or [], {"duration_ms": duration_ms, "stderr": stderr_snippet}
    except Exception as exc:  # noqa: BLE001
        return None, [f"runner parse error: {exc}"], {"duration_ms": duration_ms, "stderr": stderr_snippet}


async def _run_vision_inline(
    image_bytes: bytes,
    image_index: int,
    image_count: int,
    current_draft: Dict[str, Any],
    title_hint: Optional[str],
    settings,
) -> Tuple[Optional[dict], List[str], Dict[str, Any]]:
    start = time.time()
    draft, warnings = await llm_client.call_vision_single(
        image=image_bytes,
        model_name=settings.llm_vision_model_name,
        current_draft=current_draft,
        image_index=image_index,
        image_count=image_count,
        is_final_image=image_index == image_count,
        title_hint=title_hint,
        timeout_seconds=settings.vision_timeout_seconds,
    )
    duration_ms = int((time.time() - start) * 1000)
    return draft.model_dump(), warnings, {"duration_ms": duration_ms, "stderr": ""}


async def _run_sequential_vision(
    image_bytes: List[bytes],
    title_hint: Optional[str],
    settings,
) -> Tuple[Any, List[Dict[str, Any]]]:
    attempts: List[Dict[str, Any]] = []
    current_draft: Dict[str, Any] = {
        "title": title_hint or "Untitled",
        "description": None,
        "ingredients": [],
        "steps": [],
        "prep_time_minutes": 0,
        "cook_time_minutes": 0,
        "total_time_minutes": 0,
        "servings": None,
        "tags": [],
        "source": {"type": "image", "title_hint": title_hint},
    }

    for idx, img in enumerate(image_bytes, start=1):
        retries = 0
        max_retries = settings.vision_subprocess_max_retries
        last_error = None
        while retries <= max_retries:
            if settings.vision_subprocess_enabled:
                draft_dict, warnings, meta = await _run_vision_subprocess(
                    image_bytes=img,
                    image_index=idx,
                    image_count=len(image_bytes),
                    current_draft=current_draft,
                    title_hint=title_hint,
                    settings=settings,
                )
            else:
                draft_dict, warnings, meta = await _run_vision_inline(
                    image_bytes=img,
                    image_index=idx,
                    image_count=len(image_bytes),
                    current_draft=current_draft,
                    title_hint=title_hint,
                    settings=settings,
                )
            status = "success"
            if draft_dict is None:
                status = "failed"
                last_error = "; ".join(warnings) if warnings else "unknown"
            attempts.append(
                {
                    "tier": 3,
                    "image_index": idx,
                    "status": status,
                    "duration_ms": meta.get("duration_ms"),
                    "warnings": warnings,
                    "retry": retries,
                    "stderr": meta.get("stderr"),
                }
            )
            if draft_dict is not None:
                current_draft = draft_dict
                break
            retries += 1
        else:
            # exceeded retries
            raise ValueError(f"vision failed on image {idx}: {last_error}")

    final_draft = RecipeDraft.model_validate(current_draft)
    return final_draft, attempts
async def _run_pipeline_and_record(
    db: Session, ingestion: models.RecipeIngestion, image_bytes: List[bytes], tier_max: int
) -> Tuple[bool, Optional[RecipeDraft], Dict[str, Any]]:
    settings = get_settings()
    try:
        draft, pipeline_json, texts = await run_ingestion_pipeline(ingestion, image_bytes, tier_max)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Pipeline failed for ingestion %s: %s", ingestion.id, exc)
        ingestion.status = "FAILED"
        ingestion.pipeline_json = {"error": str(exc)}
        db.commit()
        db.refresh(ingestion)
        mailbox_service.publish(
            db,
            ingestion.user_id,
            "recipe_image_ingestion_failed",
            {
                "ingestion_id": ingestion.id,
                "error_code": "vision_failed",
                "message": "Failed to extract recipe from images",
            },
        )
        return False, None, pipeline_json

    attempts = pipeline_json.get("attempts", []) if isinstance(pipeline_json, dict) else []

    # If no draft from OCR tiers and vision is allowed, run sequential vision
    pipeline_json = pipeline_json or {}
    if draft is None and tier_max >= 3 and settings.recipe_ocr_vision_enabled:
        try:
            draft_obj, vision_attempts = await _run_sequential_vision(
                image_bytes=image_bytes,
                title_hint=ingestion.title_hint,
                settings=settings,
            )
            draft = draft_obj
            attempts.extend(vision_attempts)
            pipeline_json["attempts"] = attempts
            pipeline_json["selected_tier"] = 3
        except Exception as exc:  # noqa: BLE001
            logger.exception("Vision sequential failed for ingestion %s: %s", ingestion.id, exc)
            ingestion.status = "FAILED"
            pipeline_json = pipeline_json or {}
            pipeline_json["attempts"] = attempts
            pipeline_json["error"] = str(exc)
            db.commit()
            db.refresh(ingestion)
            mailbox_service.publish(
                db,
                ingestion.user_id,
                "recipe_image_ingestion_failed",
                {
                    "ingestion_id": ingestion.id,
                    "error_code": "vision_failed",
                    "message": "Failed to extract recipe from images",
                },
            )
            return False

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
    db.commit()
    db.refresh(ingestion)

    try:
        image_bytes = await _load_images_from_s3(ingestion.image_s3_keys or [])
    except Exception as exc:  # noqa: BLE001
        parse_job_service.mark_error(db, job, "invalid_images", "Unable to load images from storage")
        ingestion.status = "FAILED"
        db.commit()
        db.refresh(ingestion)
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
        return

    tier_max = payload.get("tier_max") or ingestion.tier_max or 3
    success, final_draft, pipeline_json = await _run_pipeline_and_record(db, ingestion, image_bytes, tier_max)
    logger.info(
        "image ingestion job finished",
        extra={"job_id": job.id, "ingestion_id": ingestion.id, "success": success, "status": job.status},
    )
    if success:
        result_payload = {"recipe_draft": final_draft.model_dump() if final_draft else None, "pipeline": pipeline_json}
        parse_job_service.mark_complete(
            db,
            job,
            type(
                "Result",
                (),
                {
                    "model_dump_json": lambda self=None: json.dumps(result_payload),
                    "error_code": None,
                    "error_message": None,
                },
            )(),
        )
    else:
        parse_job_service.mark_error(db, job, "ingestion_failed", "Image ingestion failed")

