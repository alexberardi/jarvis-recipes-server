from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from jarvis_recipes.app.api.deps import get_current_user, get_db_session
from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.schemas.planner import (
    MealPlanCreate,
    MealPlanRead,
    PlannerDraftRequest,
    PlannerDraftResponse,
)
from jarvis_recipes.app.services import planner_service

router = APIRouter(prefix="/planner", tags=["planner"])


@router.post("/draft", response_model=PlannerDraftResponse)
def draft_plan(
    payload: PlannerDraftRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    return planner_service.draft_plan(payload)


@router.post("/commit", response_model=MealPlanRead)
def commit_plan(
    payload: MealPlanCreate,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    return planner_service.commit_plan(db, current_user.id, payload)


@router.get("/current", response_model=MealPlanRead | dict)
def get_current_plan(
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    plan = planner_service.get_current_plan(db, current_user.id)
    if not plan:
        return {}
    return plan

