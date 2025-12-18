import asyncio
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from jarvis_recipes.app.db import models
from jarvis_recipes.app.schemas.meal_plan import (
    MealPlanGenerateRequest,
    MealPlanResult,
    MealType,
    DayResult,
    MealSlotResult,
    Selection,
    Alternative,
)
from jarvis_recipes.app.services import llm_client, mailbox_service, recipes_service, static_recipe_service

MEAL_ORDER: List[MealType] = ["breakfast", "lunch", "dinner", "snack", "dessert"]


def search_recipes(
    db: Session,
    user_id: str,
    meal_type: MealType,
    tags_any: List[str],
    tags_all: List[str],
    include_terms: List[str],
    exclude_terms: List[str],
    max_prep_minutes: Optional[int],
    max_cook_minutes: Optional[int],
    exclude_recipe_ids: List[str],
    limit: int = 25,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    # User recipes
    q = db.query(models.Recipe).filter(models.Recipe.user_id == user_id)
    if include_terms:
        for term in include_terms[:5]:
            like = f"%{term}%"
            q = q.filter((models.Recipe.title.ilike(like)) | (models.Recipe.description.ilike(like)))
    if exclude_terms:
        for term in exclude_terms[:5]:
            like = f"%{term}%"
            q = q.filter(~models.Recipe.title.ilike(like))
    user_recipes = q.limit(limit).all()
    for r in user_recipes:
        if str(r.id) in exclude_recipe_ids:
            continue
        tag_names = [t.name for t in r.tags] if hasattr(r, "tags") else []
        if tags_any and not set(tags_any).intersection(tag_names):
            continue
        results.append(
            {
                "id": str(r.id),
                "source": "user",
                "title": r.title,
                "description": r.description,
                "tags": tag_names,
                "prep_time_minutes": 0,
                "cook_time_minutes": r.total_time_minutes,
            }
        )

    # Core (stock) recipes - always include
    base_path = Path(__file__).resolve().parents[3] / "static_data"
    stock = static_recipe_service.list_stock_recipes(base_path, None, limit=limit)
    for s in stock:
        if s.get("id") in exclude_recipe_ids:
            continue
        tags = s.get("tags") or []
        if tags_any and not set(tags_any).intersection(tags):
            continue
        results.append(
            {
                "id": s.get("id"),
                "source": "core",
                "title": s.get("title"),
                "description": s.get("description"),
                "tags": tags,
                "prep_time_minutes": s.get("prep_time_minutes") or 0,
                "cook_time_minutes": s.get("cook_time_minutes") or 0,
            }
        )

    return results[:limit]


def get_recipe_details(db: Session, user_id: str, source: str, recipe_id: str) -> Optional[Dict[str, Any]]:
    if source == "user":
        recipe = db.get(models.Recipe, int(recipe_id))
        if not recipe or recipe.user_id != user_id:
            return None
        return {
            "id": recipe_id,
            "title": recipe.title,
            "description": recipe.description,
            "yield": None,
            "prep_time_minutes": 0,
            "cook_time_minutes": recipe.total_time_minutes or 0,
            "ingredients": [{"text": ing.text, "section": None} for ing in recipe.ingredients],
            "steps": [{"text": step.text, "section": None} for step in recipe.steps],
            "tags": [t.name for t in recipe.tags] if hasattr(recipe, "tags") else [],
            "notes": [],
        }
    if source == "stage":
        stage = db.get(models.StageRecipe, recipe_id)
        if not stage or stage.user_id != user_id:
            return None
        return {
            "id": stage.id,
            "title": stage.title,
            "description": stage.description,
            "yield": stage.yield_text,
            "prep_time_minutes": stage.prep_time_minutes or 0,
            "cook_time_minutes": stage.cook_time_minutes or 0,
            "ingredients": stage.ingredients or [],
            "steps": stage.steps or [],
            "tags": stage.tags or [],
            "notes": stage.notes or [],
        }
    if source == "core":
        base_path = Path(__file__).resolve().parents[3] / "static_data"
        stock = static_recipe_service.list_stock_recipes(base_path, None, limit=500)
        for s in stock:
            if s.get("id") == recipe_id:
                return {
                    "id": s.get("id"),
                    "title": s.get("title"),
                    "description": s.get("description"),
                    "yield": None,
                    "prep_time_minutes": s.get("prep_time_minutes") or 0,
                    "cook_time_minutes": s.get("cook_time_minutes") or 0,
                    "ingredients": [{"text": t, "section": None} for t in s.get("ingredients") or []],
                    "steps": [{"text": t, "section": None} for t in s.get("steps") or []],
                    "tags": s.get("tags") or [],
                    "notes": [],
                }
    return None


def create_stage_recipe(db: Session, user_id: str, source_recipe: Dict[str, Any], request_id: str) -> str:
    expires_at = datetime.utcnow() + timedelta(hours=72)
    stage_id = str(uuid.uuid4())
    stage = models.StageRecipe(
        id=stage_id,
        user_id=user_id,
        title=source_recipe.get("title") or "Untitled",
        description=source_recipe.get("description"),
        yield_text=source_recipe.get("yield"),
        prep_time_minutes=source_recipe.get("prep_time_minutes") or 0,
        cook_time_minutes=source_recipe.get("cook_time_minutes") or 0,
        ingredients=source_recipe.get("ingredients") or [],
        steps=source_recipe.get("steps") or [],
        tags=source_recipe.get("tags") or [],
        notes=source_recipe.get("notes") or [],
        request_id=request_id,
        created_at=datetime.utcnow(),
        expires_at=expires_at,
    )
    db.add(stage)
    db.commit()
    db.refresh(stage)
    return stage.id


def _build_terms(slot) -> Dict[str, List[str]]:
    include_terms: List[str] = []
    exclude_terms: List[str] = []
    if slot.note:
        include_terms.append(slot.note)
    return {"include_terms": include_terms, "exclude_terms": exclude_terms}


def get_recent_meals(
    db: Session,
    user_id: str,
    lookback_days: int = 7,
    before_date: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch recent meals for the user to encourage variety in meal planning.
    
    For now, this is a stub that returns an empty list.
    In the future, this could:
    - Pull from past meal plan results (if we persist them)
    - Pull from user recipe usage history
    - Pull from a dedicated meal history table
    
    Returns:
        List of recent meals with format:
        [
            {
                "date": "2025-12-14",
                "meal_type": "dinner",
                "recipe_id": "core_123",
                "title": "Grilled Chicken Bowl",
                "tags": ["chicken", "healthy"]
            }
        ]
    """
    # TODO: Implement actual meal history retrieval when we persist meal plans
    # For now, return empty list
    return []


def generate_meal_plan(
    db: Session,
    user_id: str,
    req: MealPlanGenerateRequest,
    request_id: str,
    progress_every: int = 3,
    search_fn=search_recipes,
    details_fn=get_recipe_details,
    stage_fn=create_stage_recipe,
    use_llm: bool = True,
) -> Tuple[MealPlanResult, int]:
    """
    Generate a meal plan for the user.
    
    If use_llm=True (default), the LLM will select the best recipe from candidates per slot.
    If use_llm=False, the first candidate will be selected deterministically (for testing/fallback).
    """
    days_sorted = sorted(req.days, key=lambda d: d.date)
    day_results: List[DayResult] = []
    slot_counter = 0
    progress_batch = 0
    slot_failures = 0
    used_recipe_ids: set[str] = set()
    
    # Fetch recent meals for variety control
    recent_meals = get_recent_meals(db, user_id, lookback_days=7)
    
    for day in days_sorted:
        meal_results: Dict[str, MealSlotResult] = {}
        for meal_key in MEAL_ORDER:
            slot = day.meals.get(meal_key)
            if not slot:
                continue
            slot_counter += 1
            terms = _build_terms(slot)
            candidates = search_fn(
                db=db,
                user_id=user_id,
                meal_type=meal_key,  # type: ignore[arg-type]
                tags_any=slot.tags or req.preferences.soft.tags,
                tags_all=[],
                include_terms=terms["include_terms"],
                exclude_terms=req.preferences.hard.excluded_ingredients + terms["exclude_terms"],
                max_prep_minutes=req.preferences.soft.max_prep_minutes,
                max_cook_minutes=req.preferences.soft.max_cook_minutes,
                exclude_recipe_ids=list(used_recipe_ids),
                limit=25,
            )
            
            # Debug logging
            import logging
            logger = logging.getLogger(__name__)
            logger.info(
                f"Slot {slot_counter} ({day.date} {meal_key}): "
                f"tags={slot.tags}, found {len(candidates)} candidates, "
                f"excluded={len(used_recipe_ids)} recipes"
            )
            selection = None
            selection_warnings: List[str] = []
            cand = None
            confidence = None
            
            if candidates:
                # Use LLM to select the best candidate
                if use_llm:
                    slot_data = {
                        "date": str(day.date),
                        "meal_type": meal_key,
                        "servings": slot.servings,
                        "tags": slot.tags or [],
                        "notes": slot.note,
                        "is_meal_prep": slot.is_meal_prep,
                    }
                    preferences_data = {
                        "diet": None,
                        "excluded_ingredients": req.preferences.hard.excluded_ingredients,
                        "max_prep_minutes": req.preferences.soft.max_prep_minutes,
                        "max_cook_minutes": req.preferences.soft.max_cook_minutes,
                    }
                    
                    # Call LLM selection (sync wrapper for async call)
                    llm_result = asyncio.run(
                        llm_client.call_meal_plan_select(
                            slot=slot_data,
                            preferences=preferences_data,
                            recent_meals=recent_meals,
                            candidates=candidates,
                        )
                    )
                    
                    selected_id = llm_result.get("selected_recipe_id")
                    confidence = llm_result.get("confidence", 0.5)
                    selection_warnings = llm_result.get("warnings", [])
                    llm_alternatives = llm_result.get("alternatives", [])  # List of {recipe_id, confidence, reason}
                    
                    # Check if LLM failed (connection error, unavailable, etc.)
                    llm_failed = any(w in ["LLM error", "LLM unavailable", "Empty LLM response"] for w in selection_warnings)
                    
                    if selected_id:
                        # Find the selected candidate
                        cand = next((c for c in candidates if c.get("id") == selected_id), None)
                        if not cand:
                            # Fallback if LLM selected invalid ID (shouldn't happen with validation)
                            slot_failures += 1
                    elif llm_failed and candidates:
                        # LLM failed to connect - fall back to deterministic selection
                        cand = candidates[0]
                        confidence = None
                        selection_warnings.append("LLM unavailable, using deterministic selection")
                        # Use remaining candidates as alternatives
                        llm_alternatives = [
                            {"recipe_id": c.get("id"), "confidence": 0.5, "reason": "Alternative option"}
                            for c in candidates[1:3]
                        ]
                    else:
                        # LLM intentionally returned null (no good fit)
                        slot_failures += 1
                else:
                    # Deterministic fallback: pick first candidate
                    cand = candidates[0]
                
                if cand:
                    source = cand.get("source") or "user"
                    recipe_id = cand.get("id")
                    details = details_fn(db=db, user_id=user_id, source=source, recipe_id=recipe_id)
                    selection_recipe_id = recipe_id
                    if source == "core" and details:
                        selection_recipe_id = stage_fn(db=db, user_id=user_id, source_recipe=details, request_id=request_id)
                        selection_source = "stage"
                    else:
                        selection_source = source
                    
                    # Build alternatives list
                    alternatives_list = []
                    for alt in llm_alternatives:
                        alt_id = alt.get("recipe_id")
                        alt_cand = next((c for c in candidates if c.get("id") == alt_id), None)
                        if alt_cand:
                            alt_source = alt_cand.get("source") or "user"
                            alt_recipe_id = alt_id
                            # Stage core recipes for alternatives too
                            if alt_source == "core":
                                alt_details = details_fn(db=db, user_id=user_id, source=alt_source, recipe_id=alt_recipe_id)
                                if alt_details:
                                    alt_recipe_id = stage_fn(db=db, user_id=user_id, source_recipe=alt_details, request_id=request_id)
                                    alt_source = "stage"
                            
                            alternatives_list.append(Alternative(
                                source=alt_source,  # type: ignore[arg-type]
                                recipe_id=alt_recipe_id,
                                title=alt_cand.get("title", ""),
                                confidence=alt.get("confidence", 0.5),
                                reason=alt.get("reason"),
                                matched_tags=alt_cand.get("tags") or [],
                            ))
                    
                    selection = Selection(
                        source=selection_source,  # type: ignore[arg-type]
                        recipe_id=selection_recipe_id,
                        confidence=confidence,
                        matched_tags=cand.get("tags") or [],
                        warnings=selection_warnings,
                        alternatives=alternatives_list,
                    )
                    used_recipe_ids.add(selection_recipe_id)
            else:
                slot_failures += 1
            
            meal_results[meal_key] = MealSlotResult(**slot.model_dump(), selection=selection)
            progress_batch += 1
            if progress_batch >= progress_every:
                mailbox_service.publish(
                    db,
                    user_id,
                    "meal_plan_generation_progress",
                    {"request_id": request_id, "processed_slots": slot_counter},
                )
                progress_batch = 0
        day_results.append(DayResult(date=day.date, meals=meal_results))  # type: ignore[arg-type]

    return MealPlanResult(days=day_results), slot_failures


def publish_completed(db: Session, user_id: str, request_id: str, result: MealPlanResult, slot_failures: int) -> None:
    status = "partial" if slot_failures > 0 else "completed"
    result_payload = result.model_dump(mode="json")
    mailbox_service.publish(
        db,
        user_id,
        "meal_plan_generation_completed",
        {
            "request_id": request_id,
            "status": status,
            "slot_failures_count": slot_failures,
            "result": result_payload,
        },
    )


def publish_failed(db: Session, user_id: str, request_id: str, error_code: str, message: str) -> None:
    mailbox_service.publish(
        db,
        user_id,
        "meal_plan_generation_failed",
        {"request_id": request_id, "error_code": error_code, "message": message},
    )


def cleanup_expired_stage_recipes(db: Session, cutoff_hours: int = 72, mark_jobs: bool = False) -> Tuple[int, int]:
    now = datetime.utcnow()
    expired = (
        db.query(models.StageRecipe)
        .filter(models.StageRecipe.expires_at <= now)
        .all()
    )
    request_ids = {s.request_id for s in expired if s.request_id}
    deleted = 0
    if expired:
        deleted = (
            db.query(models.StageRecipe)
            .filter(models.StageRecipe.id.in_([s.id for s in expired]))
            .delete(synchronize_session=False)
        )
    abandoned_jobs = 0
    if mark_jobs and request_ids:
        jobs = (
            db.query(models.RecipeParseJob)
            .filter(models.RecipeParseJob.job_type == "meal_plan_generate")
            .all()
        )
        for job in jobs:
            data = job.job_data or {}
            if data.get("request_id") in request_ids and job.status not in {
                "ABANDONED",
                "COMPLETED",
            }:
                job.status = "ABANDONED"
                job.abandoned_at = now
                abandoned_jobs += 1
        db.commit()
    db.commit()
    return deleted or 0, abandoned_jobs

