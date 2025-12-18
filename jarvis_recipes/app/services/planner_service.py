from datetime import timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from jarvis_recipes.app.db import models
from jarvis_recipes.app.schemas.planner import (
    MealPlanCreate,
    PlannerDraftItem,
    PlannerDraftRequest,
    PlannerDraftResponse,
)


def _ensure_user(db: Session, user_id: int) -> models.User:
    user_id_str = str(user_id)
    user = db.get(models.User, user_id_str)
    if user is None:
        user = models.User(user_id=user_id_str)
        db.add(user)
        db.flush()
    return user


def draft_plan(data: PlannerDraftRequest) -> PlannerDraftResponse:
    items = []
    meal_types = ["breakfast", "lunch", "dinner"]
    current = data.start_date
    while current <= data.end_date:
        for meal in meal_types:
            items.append(PlannerDraftItem(date=current, meal_type=meal, title=f"{meal.title()} idea"))
        current += timedelta(days=1)
    return PlannerDraftResponse(items=items)


def commit_plan(db: Session, user_id: str, data: MealPlanCreate) -> models.MealPlan:
    _ensure_user(db, user_id)
    meal_plan = models.MealPlan(user_id=str(user_id), name=data.name, start_date=data.start_date)
    for item in data.items:
        meal_plan.items.append(
            models.MealPlanItem(
                date=item.date,
                meal_type=item.meal_type,
                recipe_id=item.recipe_id,
            )
        )
    db.add(meal_plan)
    db.commit()
    db.refresh(meal_plan)
    return meal_plan


def get_current_plan(db: Session, user_id: str) -> Optional[models.MealPlan]:
    stmt = (
        select(models.MealPlan)
        .where(models.MealPlan.user_id == str(user_id))
        .order_by(models.MealPlan.created_at.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()

