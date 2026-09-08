from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from jarvis_recipes.app.api.deps import get_current_user, get_db_session
from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.schemas.planner import (
    MealPlanCreate,
    MealPlanRead,
    MealPlanSummary,
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
    return planner_service.commit_plan(db, current_user, payload)


@router.get("/current", response_model=MealPlanRead | dict)
def get_current_plan(
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    plan = planner_service.get_current_plan(db, current_user)
    if not plan:
        return {}
    return plan


@router.get("/plans", response_model=list[MealPlanSummary])
def list_plans(
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Every saved plan the household can see, newest first."""
    return planner_service.list_plans(db, current_user)


# Declared after /plans so "plans" is never taken as a {plan_id}. FastAPI matches
# in declaration order, and an int path param would 422 on it rather than fall
# through, which reads as a broken endpoint.
@router.get("/plans/{plan_id}", response_model=MealPlanRead)
def get_plan(
    plan_id: int,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    return planner_service.get_plan(db, current_user, plan_id)


@router.delete("/plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_plan(
    plan_id: int,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    planner_service.delete_plan(db, current_user, plan_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
