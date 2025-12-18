import time
from typing import Dict, List, Optional, Tuple

from PIL import Image

from jarvis_recipes.app.core.config import get_settings
from jarvis_recipes.app.db import models
from jarvis_recipes.app.schemas.ingestion import RecipeDraft
from jarvis_recipes.app.schemas.recipe import RecipeCreate
from jarvis_recipes.app.services import llm_client, ocr_easyocr, ocr_quality, ocr_tesseract


def _open_image_bytes(data: bytes) -> Image.Image:
    from io import BytesIO

    return Image.open(BytesIO(data)).convert("RGB")


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


async def run_ingestion_pipeline(
    ingestion: models.RecipeIngestion, image_bytes: List[bytes], tier_max: int
) -> Tuple[Optional[RecipeDraft], Dict, Dict]:
    settings = get_settings()
    pipeline_attempts = []
    draft: Optional[RecipeDraft] = None
    selected_tier: Optional[int] = None
    tier1_text = None
    tier2_text = None
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

    # Tier 1
    if draft is None and tier_max >= 1 and settings.recipe_ocr_tesseract_enabled:
        start = time.time()
        texts = []
        confs = []
        for data in image_bytes:
            img = _open_image_bytes(data)
            txt, metrics = ocr_tesseract.run_tesseract(img)
            texts.append(txt)
            if metrics.get("confidence") is not None:
                confs.append(metrics["confidence"])
        combined_text = "\n\n".join(texts)
        tier1_text = combined_text
        mean_conf = sum(confs) / len(confs) if confs else None
        q = ocr_quality.score_quality(combined_text, mean_conf)
        duration = int((time.time() - start) * 1000)
        status_str = "success" if q["pass_gate"] else "failed_quality"
        record_attempt(
            1,
            status_str,
            duration,
            {
                "confidence": mean_conf,
                "char_count": q["char_count"],
                "gibberish": q.get("gibberish"),
            },
            None,
        )
        if q["pass_gate"]:
            try:
                draft = await llm_client.call_text_structuring(combined_text, settings.llm_full_model_name)
                draft.validate_minimums()
                selected_tier = 1
            except Exception as ex:  # noqa: BLE001
                draft = None
                record_attempt(
                    1,
                    "failed_validation",
                    duration,
                    {
                        "confidence": mean_conf,
                        "char_count": q["char_count"],
                        "gibberish": q.get("gibberish"),
                    },
                    str(ex),
                )
        else:
            if q.get("gibberish"):
                if "gibberish_ocr" not in pipeline_json_local["warnings"]:
                    pipeline_json_local["warnings"].append("gibberish_ocr")

    # Tier 2
    if draft is None and tier_max >= 2 and settings.recipe_ocr_tier2_enabled:
        start = time.time()
        texts = []
        confs = []
        for data in image_bytes:
            img = _open_image_bytes(data)
            txt, metrics = ocr_easyocr.run_easyocr(img)
            texts.append(txt)
            if metrics.get("confidence") is not None:
                confs.append(metrics["confidence"])
        combined_text = "\n\n".join(texts)
        tier2_text = combined_text
        mean_conf = sum(confs) / len(confs) if confs else None
        q = ocr_quality.score_quality(combined_text, mean_conf)
        duration = int((time.time() - start) * 1000)
        status_str = "success" if q["pass_gate"] else "failed_quality"
        record_attempt(
            2,
            status_str,
            duration,
            {
                "confidence": mean_conf,
                "char_count": q["char_count"],
                "gibberish": q.get("gibberish"),
            },
            None,
        )
        if q["pass_gate"]:
            try:
                draft = await llm_client.call_text_structuring(combined_text, settings.llm_full_model_name)
                draft.validate_minimums()
                selected_tier = 2
            except Exception as ex:  # noqa: BLE001
                draft = None
                record_attempt(
                    2,
                    "failed_validation",
                    duration,
                    {
                        "confidence": mean_conf,
                        "char_count": q["char_count"],
                        "gibberish": q.get("gibberish"),
                    },
                    str(ex),
                )
        else:
            if q.get("gibberish"):
                if "gibberish_ocr" not in pipeline_json_local["warnings"]:
                    pipeline_json_local["warnings"].append("gibberish_ocr")

    # Tier 3 vision
    if draft is None and tier_max >= 3 and settings.recipe_ocr_vision_enabled:
        # Vision handled in worker sequentially; we leave here as placeholder.
        pass

    pipeline_json = {
        "selected_tier": selected_tier,
        "attempts": pipeline_attempts,
        "image_count": len(image_bytes),
        "warnings": pipeline_json_local.get("warnings", []),
    }
    texts = {"tier1_text": tier1_text, "tier2_text": tier2_text}
    return draft, pipeline_json, texts

