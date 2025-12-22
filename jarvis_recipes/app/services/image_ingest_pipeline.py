import logging
import time
from typing import Dict, List, Optional, Tuple

from jarvis_recipes.app.core.config import get_settings
from jarvis_recipes.app.db import models
from jarvis_recipes.app.schemas.ingestion import RecipeDraft
from jarvis_recipes.app.schemas.recipe import RecipeCreate
from jarvis_recipes.app.services import llm_client, ocr_quality, ocr_service_client

logger = logging.getLogger(__name__)


def _detect_content_type(image_bytes: bytes) -> str:
    """Detect image content type from magic bytes."""
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    elif image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    elif image_bytes.startswith(b"GIF87a") or image_bytes.startswith(b"GIF89a"):
        return "image/gif"
    else:
        return "image/jpeg"  # Default fallback


def map_draft_to_recipe_create(draft: RecipeDraft) -> RecipeCreate:
    return RecipeCreate(
        title=draft.title,
        description=draft.description,
        servings=None,
        prep_time_minutes=draft.prep_time_minutes,
        cook_time_minutes=draft.cook_time_minutes,
        total_time_minutes=draft.total_time_minutes,
        source_type=models.SourceType.IMAGE,
        source_url=None,
        image_url=None,
        ingredients=[
            {"text": ing.name, "quantity_display": ing.quantity, "unit": ing.unit} for ing in draft.ingredients
        ],
        steps=[{"step_number": i + 1, "text": text} for i, text in enumerate(draft.steps)],
        tags=draft.tags,
    )


class OCRServiceUnavailableError(Exception):
    """Raised when OCR service is unavailable and job should be retried."""
    pass


async def run_ingestion_pipeline(
    ingestion: models.RecipeIngestion, image_bytes: List[bytes], tier_max: int
) -> Tuple[Optional[RecipeDraft], Dict, Dict]:
    settings = get_settings()
    pipeline_attempts = []
    draft: Optional[RecipeDraft] = None
    selected_tier: Optional[int] = None
    ocr_text = None
    pipeline_json_local = {"warnings": []}

    def record_attempt(tier: int, status_str: str, duration_ms: int, metrics: Dict, error: Optional[str]):
        pipeline_attempts.append(
            {
                "tier": tier,
                "status": status_str,
                "duration_ms": duration_ms,
                "metrics": metrics,
                "error": error,
            }
        )

    # OCR Service: Extract text from images
    if draft is None and tier_max >= 1 and settings.jarvis_ocr_service_url:
        start = time.time()
        try:
            # Detect content type from first image (assume all images are same type)
            content_type = _detect_content_type(image_bytes[0])
            
            # Call OCR service batch endpoint for all images
            # The service handles provider selection (including vision/cloud) automatically
            combined_text, mean_conf, provider_used, metadata_list = (
                await ocr_service_client.call_ocr_service_batch(
                    image_bytes_list=image_bytes,
                    provider="auto",  # Auto selects best provider with validation guardrails
                    content_type=content_type,
                    language_hints=["en"],
                    timeout_seconds=300,  # Higher timeout for batch (especially with LLM providers)
                )
            )
            
            ocr_text = combined_text
            duration = int((time.time() - start) * 1000)
            
            if not combined_text:
                # OCR service returned no text - this is a failure that should retry
                record_attempt(
                    1,
                    "failed_no_text",
                    duration,
                    {"provider_used": provider_used},
                    "OCR service returned no text",
                )
                raise OCRServiceUnavailableError("OCR service returned no text")
            
            # Quality check
            q = ocr_quality.score_quality(combined_text, mean_conf)
            status_str = "success" if q["pass_gate"] else "failed_quality"
            
            logger.info(
                "OCR quality check: pass_gate=%s, char_count=%s, gibberish=%s, provider=%s",
                q["pass_gate"],
                q["char_count"],
                q.get("gibberish"),
                provider_used,
            )
            
            record_attempt(
                1,
                status_str,
                duration,
                {
                    "confidence": mean_conf,
                    "char_count": q["char_count"],
                    "gibberish": q.get("gibberish"),
                    "provider_used": provider_used,
                },
                None,
            )
            
            if q["pass_gate"]:
                try:
                    logger.info("Attempting text structuring with lightweight model for ingestion %s", ingestion.id)
                    draft = await llm_client.call_text_structuring(combined_text, settings.llm_lightweight_model_name)
                    logger.info("Text structuring succeeded, validating draft for ingestion %s", ingestion.id)
                    draft.validate_minimums()
                    logger.info("Draft validation passed for ingestion %s", ingestion.id)
                    selected_tier = 1
                except Exception as ex:  # noqa: BLE001
                    logger.exception("Text structuring or validation failed for ingestion %s: %s", ingestion.id, ex)
                    draft = None
                    record_attempt(
                        1,
                        "failed_validation",
                        duration,
                        {
                            "confidence": mean_conf,
                            "char_count": q["char_count"],
                            "gibberish": q.get("gibberish"),
                            "provider_used": provider_used,
                        },
                        str(ex),
                    )
            else:
                logger.warning("Quality gate failed for ingestion %s: char_count=%s, gibberish=%s", ingestion.id, q["char_count"], q.get("gibberish"))
                if q.get("gibberish"):
                    if "gibberish_ocr" not in pipeline_json_local["warnings"]:
                        pipeline_json_local["warnings"].append("gibberish_ocr")
        except OCRServiceUnavailableError:
            # Re-raise to trigger job retry
            raise
        except Exception as ex:  # noqa: BLE001
            # OCR service unavailable or failed - mark for retry
            duration = int((time.time() - start) * 1000)
            record_attempt(
                1,
                "failed_service_error",
                duration,
                {},
                str(ex),
            )
            raise OCRServiceUnavailableError(f"OCR service error: {ex}") from ex

    # Note: Vision and Cloud OCR are now handled by the OCR service via
    # llm_proxy_vision and llm_proxy_cloud providers. The "auto" provider
    # selection automatically tries these if traditional OCR fails.

    pipeline_json = {
        "selected_tier": selected_tier,
        "attempts": pipeline_attempts,
        "image_count": len(image_bytes),
        "warnings": pipeline_json_local.get("warnings", []),
    }
    # Keep tier1_text/tier2_text for DB compatibility, but they're the same now
    texts = {"tier1_text": ocr_text, "tier2_text": ocr_text}
    return draft, pipeline_json, texts

