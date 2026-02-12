import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

from jarvis_recipes.app.core.config import get_settings
from jarvis_recipes.app.schemas.ingestion import RecipeDraft

logger = logging.getLogger(__name__)


# Remove ASCII control chars that frequently break json.loads (except \n, \r, \t)
def _strip_invalid_control_chars(s: str) -> str:
    """Remove ASCII control chars that frequently break json.loads (except \n, \r, \t)."""
    if not isinstance(s, str):
        return str(s)
    # Remove 0x00-0x1F excluding tab(\x09), lf(\x0A), cr(\x0D)
    return re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", s)


def _coerce_recipe_draft(obj: Any, source_type: str = "image") -> RecipeDraft:
    """
    Accepts various JSON shapes and coerces into RecipeDraft.
    Expected shape is RecipeDraft; we also accept {"recipe": {...}} with keys:
      name/title, description, ingredients[{label/name, quantity/unit, notes}], directions/steps[{text}], servings, prepTime/cookTime/totalTime
    """
    data = obj
    def _strip_code_fence(text: str) -> str:
        txt = text.strip()
        if txt.startswith("```"):
            # Remove leading fence with optional language tag
            txt = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", txt, count=1)
            # Remove trailing fence
            txt = re.sub(r"\s*```$", "", txt, count=1).strip()
        return txt

    if isinstance(obj, str):
        cleaned = _strip_code_fence(obj)
        data = json.loads(cleaned)
    if isinstance(data, dict) and "recipe" in data:
        data = data["recipe"]
    # Check for error field - only raise if it's a meaningful error (not just "{" or empty)
    if isinstance(data, dict) and data.get("error"):
        error_val = data.get("error")
        # Only treat as error if it's a non-empty string or meaningful dict
        if isinstance(error_val, str) and error_val.strip() and error_val.strip() != "{":
            raise ValueError(f"LLM returned error: {error_val}")
        elif isinstance(error_val, dict) and (error_val.get("message") or error_val.get("code")):
            raise ValueError(f"LLM returned error: {error_val}")
    # If already valid, let pydantic handle it
    try:
        draft = RecipeDraft.model_validate(data)
        draft.validate_minimums()
        return draft
    except Exception as exc:
        logger.warning("LLM draft validation (direct) failed, attempting coercion: %s", exc)

    if not isinstance(data, dict):
        raise ValueError("LLM response is not a JSON object")

    title = data.get("title") or data.get("name") or "Untitled"
    description = data.get("description")
    ingredients_in = data.get("ingredients") or []
    steps_in = data.get("steps") or data.get("directions") or []

    COMMON_UNITS = {
        "tsp",
        "teaspoon",
        "teaspoons",
        "tbsp",
        "tablespoon",
        "tablespoons",
        "cup",
        "cups",
        "oz",
        "ounce",
        "ounces",
        "lb",
        "pound",
        "pounds",
        "g",
        "gram",
        "grams",
        "kg",
        "ml",
        "l",
        "liter",
        "litre",
        "pint",
        "pt",
        "quart",
        "qt",
        "gallon",
        "gal",
        "stick",
        "clove",
        "cloves",
        "can",
        "cans",
        "package",
        "packages",
        "slice",
        "slices",
        "piece",
        "pieces",
        "inch",
        "inches",
    }

    def _extract_qty_from_text(text: str) -> Optional[Tuple[str, Optional[str], str]]:
        fraction_chars = "¼½¾⅐⅑⅒⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞"
        qty_unit_re = re.compile(rf"^\s*([\d\s\/\.\-{fraction_chars}]+)\s+([A-Za-z][A-Za-z\.\-]*)\s+(.*)$")
        qty_only_re = re.compile(rf"^\s*([\d\s\/\.\-{fraction_chars}]+)\s+(.*)$")
        m = qty_unit_re.match(text)
        if m:
            qty = m.group(1).strip()
            unit = m.group(2).strip().lower()
            name_rest = m.group(3).strip()
            if unit in COMMON_UNITS:
                return qty, unit, name_rest
        m = qty_only_re.match(text)
        if m:
            qty = m.group(1).strip()
            name_rest = m.group(2).strip()
            return qty, None, name_rest
        return None

    ingredients = []
    for ing in ingredients_in:
        if not isinstance(ing, dict):
            continue
        qty = ing.get("quantity") or ing.get("quantity_display")
        unit = ing.get("unit")
        # If quantity is a dict like {"value": 1.5, "unit": "cups"}, flatten it
        if isinstance(qty, dict):
            val = qty.get("value")
            unit = unit or qty.get("unit")
            if val is not None:
                try:
                    qty = str(val)
                except (TypeError, ValueError):
                    qty = None
        elif qty is not None and not isinstance(qty, str):
            try:
                qty = str(qty)
            except (TypeError, ValueError):
                qty = None
        name_val = ing.get("name") or ing.get("label") or ""
        ingredients.append(
            {
                "name": name_val,
                "quantity": qty,
                "unit": unit,
                "notes": ing.get("notes"),
            }
        )

    steps = []
    for st in steps_in:
        if isinstance(st, dict):
            # Handle various step formats: {"text": "..."}, {"action": "..."}, {"description": "..."}, {"label": "..."}
            step_text = st.get("text") or st.get("action") or st.get("description") or st.get("label") or ""
            step_text = step_text.strip()
            if step_text:
                steps.append(step_text)
        elif isinstance(st, str):
            step_text = st.strip()
            if step_text:
                steps.append(step_text)

    # Handle time fields with fallbacks
    prep_time = data.get("prep_time_minutes") or data.get("prepTime")
    cook_time = data.get("cook_time_minutes") or data.get("cookTime")
    total_time = data.get("total_time_minutes") or data.get("totalTime")
    active_time = data.get("activeTime") or data.get("active_time_minutes")

    # If cook_time is missing but active_time or total_time exists, use them conservatively
    if cook_time is None:
        cook_time = active_time or total_time or 0
    if prep_time is None:
        prep_time = 0
    if total_time is None and prep_time is not None and cook_time is not None:
        try:
            total_time = float(prep_time) + float(cook_time)
        except (TypeError, ValueError):
            total_time = 0

    # Normalize ingredients: extract qty/unit from quantity field or name if needed
    normalized_ingredients = []
    for ing in ingredients:
        qty = ing["quantity"]
        unit = ing["unit"]
        name = ing["name"] or ""
        
        # If quantity contains text beyond just a number (e.g., "1 cup mayonnaise" or "2 cups"),
        # extract the number and unit from it
        if qty and isinstance(qty, str):
            # Check if quantity contains both numbers and letters (indicating it has units or ingredient names)
            # We need both to ensure we're not trying to extract from a pure unit like "cup"
            has_numbers = bool(re.search(r'[\d\/¼½¾⅐⅑⅒⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞]', qty))
            has_letters = bool(re.search(r'[A-Za-z]', qty))
            if has_numbers and has_letters:
                extracted = _extract_qty_from_text(qty)
                if extracted:
                    qty_extracted, unit_extracted, name_rest = extracted
                    # Always use the extracted quantity (it should be just the number)
                    if qty_extracted:
                        qty = qty_extracted
                    # Set unit from extraction if we got one (prefer extracted over existing if both exist)
                    if unit_extracted:
                        unit = unit_extracted
                    # Only use name_rest if we don't already have a name
                    if name_rest and not name:
                        name = name_rest
                else:
                    # Extraction failed - if quantity looks like it's just a unit (no numbers), clear it
                    if not has_numbers and has_letters:
                        # This is likely a unit that got put in the quantity field by mistake
                        if not unit:
                            unit = qty
                        qty = None
        
        # If quantity is empty but name starts with qty/unit, split it out
        if (not qty or qty == "") and name:
            extracted = _extract_qty_from_text(name)
            if extracted:
                qty_extracted, unit_extracted, name_rest = extracted
                qty = qty_extracted
                unit = unit or unit_extracted
                # Preserve the name_rest, but fall back to original name if name_rest is empty
                name = name_rest if name_rest else name
        
        # Normalize empty strings to None
        if qty == "":
            qty = None
        if unit == "":
            unit = None
        if ing.get("notes") == "":
            notes = None
        else:
            notes = ing.get("notes")
        
        # Only add ingredients that have a non-empty name (required by schema)
        if name and name.strip():
            normalized_ingredients.append(
                {"name": name.strip(), "quantity": qty, "unit": unit, "notes": notes}
            )

    draft_data = {
        "title": title,
        "description": description,
        "ingredients": normalized_ingredients,
        "steps": steps,
        "prep_time_minutes": prep_time if prep_time is not None else 0,
        "cook_time_minutes": cook_time if cook_time is not None else 0,
        "total_time_minutes": total_time if total_time is not None else 0,
        "servings": data.get("servings"),
        "tags": data.get("tags") or [],
        "source": {"type": source_type},
    }
    draft = RecipeDraft.model_validate(draft_data)
    draft.validate_minimums()
    return draft


def _fallback_draft(reason: str, source_type: str = "image") -> RecipeDraft:
    # Provide a minimal, user-editable draft to avoid failing the ingestion entirely.
    placeholders = [
        {"name": "Add ingredient", "quantity": None, "unit": None, "notes": reason},
        {"name": "Add ingredient", "quantity": None, "unit": None, "notes": reason},
        {"name": "Add ingredient", "quantity": None, "unit": None, "notes": reason},
    ]
    steps = ["Add steps manually", "Add steps manually"]
    draft_data = {
        "title": "Untitled",
        "description": None,
        "ingredients": placeholders,
        "steps": steps,
        "prep_time_minutes": 0,
        "cook_time_minutes": 0,
        "total_time_minutes": 0,
        "servings": None,
        "tags": [],
        "source": {"type": source_type},
    }
    draft = RecipeDraft.model_validate(draft_data)
    return draft


def _try_local_json_repair(raw: str) -> Optional[str]:
    cleaned = _strip_invalid_control_chars(raw).strip()
    cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    if cleaned.startswith("{") and cleaned.endswith("}"):
        try:
            json.loads(cleaned)
            return cleaned
        except json.JSONDecodeError:
            pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = cleaned[start : end + 1]
        try:
            json.loads(snippet)
            return snippet
        except json.JSONDecodeError:
            return None
    return None


async def _repair_json_via_full_llm(broken_json: str, schema_hint: str, timeout_seconds: int = 60) -> Optional[str]:
    settings = get_settings()
    payload = {
        "model": settings.llm_full_model_name or "full",
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": "Repair malformed JSON to match the schema. Return ONLY valid JSON.",
            },
            {
                "role": "user",
                "content": f"Schema: {schema_hint}\nMalformed JSON:\n{broken_json}",
            },
        ],
        "max_tokens": 800,
        "stream": False,
    }
    timeout = httpx.Timeout(timeout_seconds, read=timeout_seconds, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{settings.llm_base_url}/v1/chat/completions",
            json=payload,
            headers=_headers(),
        )
    if resp.status_code >= 400:
        return None
    data = resp.json()
    
    # Check for error response from LLM proxy (per PRD: json-response-format-support.md)
    if isinstance(data, dict) and "error" in data:
        error_info = data["error"]
        error_type = error_info.get("type", "unknown_error")
        error_message = error_info.get("message", "Unknown error")
        logger.warning(
            "LLM proxy returned error in JSON repair: type=%s, message=%s",
            error_type,
            error_message[:500],
        )
        return None
    
    content = data.get("choices", [{}])[0].get("message", {}).get("content")
    if not content:
        return None
    repaired = content.strip()
    repaired = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", repaired)
    repaired = re.sub(r"\s*```$", "", repaired)
    try:
        json.loads(repaired)
        return repaired
    except json.JSONDecodeError:
        return None


def _headers() -> Dict[str, str]:
    settings = get_settings()
    if not settings.jarvis_app_id or not settings.jarvis_app_key:
        raise ValueError("JARVIS_APP_ID and JARVIS_APP_KEY must be set for LLM proxy authentication")
    return {
        "Content-Type": "application/json",
        "X-Jarvis-App-Id": settings.jarvis_app_id,
        "X-Jarvis-App-Key": settings.jarvis_app_key,
    }


async def _parse_with_repair(raw_content: str, source_type: str) -> RecipeDraft:
    raw_content = _strip_invalid_control_chars(raw_content)
    try:
        return _coerce_recipe_draft(raw_content, source_type=source_type)
    except (json.JSONDecodeError, ValueError, KeyError, TypeError):
        repaired = _try_local_json_repair(raw_content)
        if repaired:
            try:
                return _coerce_recipe_draft(repaired, source_type=source_type)
            except (json.JSONDecodeError, ValueError, KeyError, TypeError):
                pass
        schema_hint = (
            '{ "title": string, "description": string|null, "ingredients": '
            '[{"name": string, "quantity": string|null, "unit": string|null, "notes": string|null}], '
            '"steps": [string], "prep_time_minutes": number, "cook_time_minutes": number, '
            '"total_time_minutes": number, "servings": string|number|null, "tags": [string], '
            '"source": {"type":"image"|"ocr"|"url", "source_url": string|null, "image_url": string|null} }'
        )
        repaired_llm = await _repair_json_via_full_llm(raw_content, schema_hint)
        if repaired_llm:
            return _coerce_recipe_draft(repaired_llm, source_type=source_type)
        raise


async def clean_and_validate_draft(draft: RecipeDraft, model_name: str) -> RecipeDraft:
    """
    Clean and validate a recipe draft using the lightweight model.
    
    This post-processing step:
    - Separates ingredients properly (ensures each ingredient is distinct)
    - Removes units of measure from ingredient names (moves to unit field)
    - Adds description if missing
    - Validates and fixes any formatting issues
    
    Args:
        draft: The RecipeDraft to clean
        model_name: The lightweight model name to use
    
    Returns:
        Cleaned RecipeDraft
    """
    settings = get_settings()
    
    # Convert draft to JSON for the LLM
    draft_json = draft.model_dump(mode="json")
    
    payload = {
        "model": model_name or settings.llm_lightweight_model_name or "lightweight",
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "Clean recipe data. Return ONLY valid JSON matching RecipeDraft schema.\n\n"
                    "Rules:\n"
                    "- Separate ingredients: 'salt and pepper' = 2 entries\n"
                    "- Extract units from names: '1 cup flour' → quantity:'1', unit:'cup', name:'flour'\n"
                    "- Put prep notes in 'notes' field\n"
                    "- Add description if missing (1-2 sentences based on title/ingredients)\n"
                    "- Preserve all valid data, only clean formatting\n"
                ),
            },
            {
                "role": "user",
                "content": f"Clean this recipe:\n{json.dumps(draft_json, indent=2)}",
            },
        ],
        "max_tokens": 1000,
        "stream": False,
    }
    
    timeout = httpx.Timeout(30.0, read=30.0, connect=10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{settings.llm_base_url}/v1/chat/completions",
                json=payload,
                headers=_headers(),
            )
        resp.raise_for_status()
        data = resp.json()
        
        # Check for error response
        if isinstance(data, dict) and "error" in data:
            error_info = data["error"]
            error_type = error_info.get("type", "unknown_error")
            error_message = error_info.get("message", "Unknown error")
            logger.warning(
                "LLM proxy returned error in draft cleaning: type=%s, message=%s",
                error_type,
                error_message[:500],
            )
            # Return original draft if cleaning fails
            return draft
        
        content = data.get("choices", [{}])[0].get("message", {}).get("content")
        if not content:
            logger.warning("Draft cleaning returned empty content, using original draft")
            return draft
        
        # Parse the cleaned draft
        cleaned_content = content.strip()
        cleaned_content = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", cleaned_content)
        cleaned_content = re.sub(r"\s*```$", "", cleaned_content)
        
        try:
            cleaned_data = json.loads(cleaned_content)
            cleaned_draft = _coerce_recipe_draft(cleaned_data, source_type=draft.source.type if draft.source else "ocr")
            logger.info("Draft cleaned successfully: %d ingredients, %d steps", 
                       len(cleaned_draft.ingredients), len(cleaned_draft.steps))
            return cleaned_draft
        except Exception as exc:
            logger.warning("Failed to parse cleaned draft, using original: %s", exc)
            return draft
    
    except Exception as exc:
        logger.warning("Draft cleaning failed, using original draft: %s", exc)
        return draft


async def call_text_structuring(text: str, model_name: str) -> RecipeDraft:
    settings = get_settings()
    payload = {
        "model": model_name or settings.llm_full_model_name or "full",
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "Convert OCR text to RecipeDraft JSON. Return ONLY JSON.\n\n"
                    "Schema: {\"title\":string,\"description\":string|null,\"ingredients\":[{\"name\":string,\"quantity\":string|null,\"unit\":string|null,\"notes\":string|null}],"
                    "\"steps\":[string],\"prep_time_minutes\":int,\"cook_time_minutes\":int,\"total_time_minutes\":int,\"servings\":string|number|null,\"tags\":[string],\"source\":{\"type\":\"ocr\"}}\n\n"
                    "Rules:\n"
                    "- Separate ingredients: 'salt and pepper' = 2 entries\n"
                    "- Extract units from names: '1 cup flour' → quantity:'1', unit:'cup', name:'flour'\n"
                    "- Put prep notes in 'notes' field\n"
                    "- Use 0 for unknown time fields, null for missing description\n"
                    "- If not a valid recipe, return {\"error\":\"garbage_ocr\"}\n"
                ),
            },
            {
                "role": "user",
                "content": f"OCR TEXT (verbatim):\n<<<OCR_START>>>\n{text}\n<<<OCR_END>>>",
            },
        ],
        "max_tokens": 1100,
        "stream": False,
    }
    timeout = httpx.Timeout(60.0, read=60.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{settings.llm_base_url}/v1/chat/completions",
            json=payload,
            headers=_headers(),
        )
    resp.raise_for_status()
    data = resp.json()
    
    # Check for error response from LLM proxy (per PRD: json-response-format-support.md)
    if isinstance(data, dict) and "error" in data:
        error_info = data["error"]
        error_type = error_info.get("type", "unknown_error")
        error_message = error_info.get("message", "Unknown error")
        logger.error(
            "LLM proxy returned error in text structuring: type=%s, message=%s",
            error_type,
            error_message[:500],
        )
        raise ValueError(f"LLM proxy error ({error_type}): {error_message}")
    
    content = data.get("choices", [{}])[0].get("message", {}).get("content")
    logger.info("text llm raw content: %s", content if isinstance(content, str) else str(content))
    if not content:
        raise ValueError("LLM text structuring missing content")
    raw_content = content if isinstance(content, str) else str(content)
    return await _parse_with_repair(raw_content, source_type="ocr")


# Vision and Cloud OCR are now handled by the OCR service via llm_proxy_vision and llm_proxy_cloud providers


async def call_meal_plan_select(
    slot: Dict[str, Any],
    preferences: Dict[str, Any],
    recent_meals: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    model_name: Optional[str] = None,
    timeout_seconds: int = 30,
) -> Dict[str, Any]:
    """
    Invoke LLM to select the best recipe from candidates for a meal slot.
    
    Returns:
        {
            "selected_recipe_id": str|None,
            "confidence": float (0.0-1.0),
            "reason": str (optional),
            "warnings": [str]
        }
    """
    settings = get_settings()
    
    # Build candidate summaries (limit detail for token efficiency)
    candidate_summaries = [
        {
            "recipe_id": c.get("id"),
            "title": c.get("title"),
            "tags": c.get("tags", []),
            "prep_time": c.get("prep_time_minutes"),
            "cook_time": c.get("cook_time_minutes"),
            "summary": c.get("description", "")[:100] if c.get("description") else None,
        }
        for c in candidates[:25]
    ]
    
    prompt_input = {
        "slot": slot,
        "preferences": preferences,
        "recent_meals": recent_meals,
        "candidates": candidate_summaries,
    }
    
    system_prompt = (
        "You are a meal planning assistant that selects and ranks recipes from a provided candidate list. "
        "Your job is to interpret user intent (notes, tags, preferences), encourage variety using recent meal history, "
        "and return the TOP 3 RANKED recipes that best fit the slot. "
        "\n\nRULES:\n"
        "- Return your TOP 3 recipe choices in ranked order (best first).\n"
        "- You MUST select recipe_ids from the provided candidates list.\n"
        "- You MUST NOT invent, create, or select recipes not in the candidates list.\n"
        "- Prefer to suggest options over nothing - even if not perfect matches.\n"
        "- ONLY return null for the primary selection if candidates are truly incompatible (e.g., user wants vegan but all candidates have meat).\n"
        "- Variety is a soft constraint: prefer different recipes/proteins across consecutive days when alternatives exist.\n"
        "- Interpret free-text notes (e.g., 'something easy', 'I want chicken') as ranking signals, not hard requirements.\n"
        "- Tags and preferences are guidance, not absolute filters - be flexible and helpful.\n"
        "- Use confidence scores to indicate match quality: 0.9-1.0 = excellent, 0.7-0.9 = good, 0.5-0.7 = acceptable, <0.5 = poor.\n"
        "- For each ranked option, provide a brief reason explaining why it's a good choice.\n"
        "- Return ONLY valid JSON matching this schema:\n"
        '  { "ranked_recipes": [{"recipe_id": "string", "confidence": 0.0-1.0, "reason": "why this fits"}], '
        '"warnings": ["optional warning strings"] }\n'
        "- The ranked_recipes array should contain 1-3 recipes in priority order.\n"
        "- If no candidates fit at all, return ranked_recipes as an empty array with warnings explaining why."
    )
    
    user_prompt = (
        f"Select the best recipe for this meal slot:\n{json.dumps(prompt_input, indent=2)}\n\n"
        "Return your selection as JSON only. No prose, no markdown."
    )
    
    payload = {
        "model": model_name or settings.llm_full_model_name or "full",
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 300,
        "stream": False,
    }
    
    timeout = httpx.Timeout(timeout_seconds, read=timeout_seconds, connect=10.0)
    
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{settings.llm_base_url}/v1/chat/completions",
                json=payload,
                headers=_headers(),
            )
        
        if resp.status_code >= 400:
            logger.warning("Meal plan LLM selection failed with status %s", resp.status_code)
            return {
                "selected_recipe_id": None,
                "confidence": 0.0,
                "reason": f"LLM request failed: {resp.status_code}",
                "warnings": ["LLM unavailable"],
            }
        
        data = resp.json()
        
        # Check for error response from LLM proxy (per PRD: json-response-format-support.md)
        if isinstance(data, dict) and "error" in data:
            error_info = data["error"]
            error_type = error_info.get("type", "unknown_error")
            error_message = error_info.get("message", "Unknown error")
            logger.warning(
                "LLM proxy returned error in meal plan selection: type=%s, message=%s",
                error_type,
                error_message[:500],
            )
            return {
                "selected_recipe_id": None,
                "confidence": 0.0,
                "reason": f"LLM proxy error ({error_type})",
                "warnings": ["LLM proxy error"],
            }
        
        content = data.get("choices", [{}])[0].get("message", {}).get("content")
        
        if not content:
            logger.warning("Meal plan LLM returned empty content")
            return {
                "selected_recipe_id": None,
                "confidence": 0.0,
                "reason": "LLM returned empty response",
                "warnings": ["Empty LLM response"],
                "alternatives": [],
            }
        
        # Parse and validate
        result = json.loads(content)
        
        # Extract ranked recipes and warnings
        ranked_recipes = result.get("ranked_recipes", [])
        warnings = result.get("warnings", [])
        
        # Validate all recipe IDs are in candidates
        candidate_ids = {c.get("id") for c in candidates}
        validated_ranked = []
        
        for idx, ranked in enumerate(ranked_recipes[:3]):  # Max 3
            recipe_id = ranked.get("recipe_id")
            if recipe_id and recipe_id in candidate_ids:
                confidence = ranked.get("confidence", 0.5)
                if not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1:
                    confidence = 0.5
                
                validated_ranked.append({
                    "recipe_id": recipe_id,
                    "confidence": float(confidence),
                    "reason": ranked.get("reason", ""),
                })
            else:
                logger.warning(
                    "LLM ranked recipe_id=%s not in candidates, skipping",
                    recipe_id,
                )
        
        # If no valid ranked recipes, return null selection
        if not validated_ranked:
                return {
                    "selected_recipe_id": None,
                    "confidence": 0.0,
                "reason": "No valid recipes returned by LLM",
                "warnings": warnings + ["LLM returned no valid selections"],
                "alternatives": [],
                }
        
        # Primary selection is the first ranked recipe
        primary = validated_ranked[0]
        alternatives = validated_ranked[1:]  # Rest are alternatives
        
        return {
            "selected_recipe_id": primary["recipe_id"],
            "confidence": primary["confidence"],
            "reason": primary["reason"],
            "warnings": warnings,
            "alternatives": alternatives,  # List of {recipe_id, confidence, reason}
        }
    
    except Exception as exc:  # noqa: BLE001
        logger.warning("Meal plan LLM selection exception: %s", exc)
        return {
            "selected_recipe_id": None,
            "confidence": 0.0,
            "reason": f"Exception: {exc}",
            "warnings": ["LLM error"],
            "alternatives": [],
        }

