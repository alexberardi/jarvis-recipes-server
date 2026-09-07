import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from jarvis_recipes.app.api.deps import get_current_user, get_db_session
from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.schemas.meal_plan import (
    MealPlanGenerateRequest,
    RandomPlanRequest,
    RandomPlanResponse,
    RandomSlotResult,
    RerollRequest,
)
from jarvis_recipes.app.services import parse_job_service, random_plan_service
from jarvis_recipes.app.services.random_plan_service import SlotRequest

router = APIRouter(prefix="/meal-plans", tags=["meal_plans"])


@router.post("/generate/jobs", status_code=202)
def enqueue_meal_plan_generation(
    payload: MealPlanGenerateRequest,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    request_id = str(uuid.uuid4())
    try:
        job = parse_job_service.create_job(
            db=db,
            user_id=str(current_user.id),
            job_type="meal_plan_generate",
            job_data={"request_id": request_id, "payload": payload.model_dump(mode="json")},
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))
    return {"job_id": job.id, "request_id": request_id}


@router.get("/generate/jobs/{job_id}")
def get_meal_plan_job(
    job_id: str,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    job = parse_job_service.get_job_for_user(db, job_id, current_user.id)
    if not job or job.job_type != "meal_plan_generate":
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "id": job.id,
        "status": job.status,
        "result": job.result_json,
        "error_code": job.error_code,
        "error_message": job.error_message,
    }



def _slot_result(date, meal_type: str, recipe) -> RandomSlotResult:
    if recipe is None:
        return RandomSlotResult(date=date, meal_type=meal_type)
    return RandomSlotResult(
        date=date,
        meal_type=meal_type,
        recipe_id=recipe.id,
        title=recipe.title,
        image_url=recipe.image_url,
        total_time_minutes=recipe.total_time_minutes,
        servings=recipe.servings,
    )


@router.post("/random", response_model=RandomPlanResponse)
def random_plan(
    payload: RandomPlanRequest,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Fill a week from the household's box, immediately.

    Synchronous on purpose. The LLM planner is a queued job with a progress
    screen because it has to think; this does not, and making the person watch a
    spinner for a shuffle would be the whole complaint about meal planning in
    miniature.
    """
    slots = [SlotRequest(date=s.date, meal_type=s.meal_type) for s in payload.slots]
    filled = random_plan_service.pick_plan(
        db, current_user, slots, exclude_ids=payload.exclude_recipe_ids
    )
    results = [_slot_result(slot.date, slot.meal_type, recipe) for slot, recipe in filled]
    return RandomPlanResponse(
        slots=results,
        incomplete=any(r.recipe_id is None for r in results),
    )


@router.post("/random/reroll", response_model=RandomSlotResult)
def reroll_slot(
    payload: RerollRequest,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Re-roll one slot.

    The client sends everything currently on screen in exclude_recipe_ids, so the
    replacement is neither the rejected recipe nor a duplicate of another slot.
    """
    recipe = random_plan_service.pick_one(
        db, current_user, payload.meal_type, payload.exclude_recipe_ids, payload.tags
    )
    if recipe is None:
        raise HTTPException(
            status_code=409,
            detail="No other recipe available to swap in. Add more recipes, or clear a slot.",
        )
    return _slot_result(None, payload.meal_type or "", recipe)
