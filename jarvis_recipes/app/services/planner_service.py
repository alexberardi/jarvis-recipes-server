from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func, select
from fastapi import HTTPException, status
from sqlalchemy.orm import Session, joinedload, selectinload

from jarvis_recipes.app.db import models
from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.services.scoping import household_for_write, visible_to
from jarvis_recipes.app.services.user_service import ensure_user
from jarvis_recipes.app.schemas.planner import (
    MealPlanCreate,
    MealPlanSummary,
    PlannerDraftItem,
    PlannerDraftRequest,
    PlannerDraftResponse,
)


def draft_plan(data: PlannerDraftRequest) -> PlannerDraftResponse:
    items = []
    meal_types = ["breakfast", "lunch", "dinner"]
    current = data.start_date
    while current <= data.end_date:
        for meal in meal_types:
            items.append(PlannerDraftItem(date=current, meal_type=meal, title=f"{meal.title()} idea"))
        current += timedelta(days=1)
    return PlannerDraftResponse(items=items)


def _materialise_stage_recipe(db: Session, user: CurrentUser, stage_id: int) -> models.Recipe:
    """Turn a planner-staged recipe into a real one in the household's box.

    Committing a plan is the moment a proposal becomes household data, so it is
    also the moment a staged pick should stop being temporary. Staged rows expire
    on a timer (cleanup_expired_stage_recipes); leaving a committed plan pointing
    at one would give the plan a shelf life, and meal_plan_items.recipe_id is an
    FK to recipes.id anyway, so it could not point there at all.
    """
    stage = db.get(models.StageRecipe, stage_id)
    if not stage or stage.user_id != str(user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Staged recipe {stage_id} not found",
        )

    recipe = models.Recipe(
        user_id=str(user.id),
        household_id=household_for_write(user),
        title=stage.title,
        description=stage.description,
        source_type=models.SourceType.MANUAL,
        servings=None,
        total_time_minutes=(stage.prep_time_minutes or 0) + (stage.cook_time_minutes or 0) or None,
    )
    for ing in stage.ingredients or []:
        text = ing.get("text") if isinstance(ing, dict) else str(ing)
        if not text:
            continue
        recipe.ingredients.append(
            models.Ingredient(
                text=text,
                quantity_display=(ing.get("quantity_display") if isinstance(ing, dict) else None),
                unit=(ing.get("unit") if isinstance(ing, dict) else None),
            )
        )
    for n, step in enumerate(stage.steps or [], start=1):
        text = step.get("text") if isinstance(step, dict) else str(step)
        if text:
            recipe.steps.append(models.Step(step_number=n, text=text))

    db.add(recipe)
    db.flush()
    return recipe


def commit_plan(db: Session, user: CurrentUser, data: MealPlanCreate) -> models.MealPlan:
    ensure_user(db, str(user.id))
    meal_plan = models.MealPlan(
        user_id=str(user.id),
        household_id=household_for_write(user),
        name=data.name,
        start_date=data.start_date,
    )
    # Staged ids are per-plan, so the same staged recipe referenced twice must
    # become ONE recipe, not two identical rows in the box.
    materialised: dict[int, int] = {}

    for item in data.items:
        recipe_id = item.recipe_id
        if getattr(item, "source", "user") == "stage":
            if item.recipe_id not in materialised:
                materialised[item.recipe_id] = _materialise_stage_recipe(
                    db, user, item.recipe_id
                ).id
            recipe_id = materialised[item.recipe_id]

        meal_plan.items.append(
            models.MealPlanItem(
                date=item.date,
                meal_type=item.meal_type,
                recipe_id=recipe_id,
            )
        )
    db.add(meal_plan)
    db.commit()
    db.refresh(meal_plan)
    return meal_plan


def get_current_plan(db: Session, user: CurrentUser) -> Optional[models.MealPlan]:
    """The plan to show for "what are we eating".

    Not simply the newest row. A plan committed on Sunday for next week should not
    be displaced by one committed on Monday for a month away, and a plan whose
    last day has passed is history, not the answer. So: among plans that still
    have a day today or later, the one that starts soonest; if none do, nothing.

    Household-scoped, because "what are we eating" is a family question.
    """
    today = date.today()
    stmt = (
        select(models.MealPlan)
        .join(models.MealPlanItem)
        .where(visible_to(models.MealPlan, user))
        .where(models.MealPlanItem.date >= today)
        .options(selectinload(models.MealPlan.items).joinedload(models.MealPlanItem.recipe))
        .order_by(func.min(models.MealPlanItem.date), models.MealPlan.created_at.desc())
        .group_by(models.MealPlan.id)
        .limit(1)
    )
    return db.scalars(stmt).first()


def list_plans(db: Session, user: CurrentUser) -> list[MealPlanSummary]:
    """Every plan the household can see, most recent first.

    Returns summaries, not whole plans: the list screen needs a span and a count,
    and shipping every item of every plan to render a few rows is a page that gets
    slower each week it is used.
    """
    stmt = (
        select(
            models.MealPlan.id,
            models.MealPlan.name,
            models.MealPlan.start_date,
            models.MealPlan.created_at,
            func.min(models.MealPlanItem.date).label("first_day"),
            func.max(models.MealPlanItem.date).label("last_day"),
            func.count(models.MealPlanItem.id).label("meal_count"),
        )
        # OUTER join: a plan with no items is a data bug, but hiding it makes that
        # bug invisible and undeletable from the UI.
        .outerjoin(models.MealPlanItem)
        .where(visible_to(models.MealPlan, user))
        .group_by(models.MealPlan.id)
        .order_by(models.MealPlan.created_at.desc())
    )
    return [
        MealPlanSummary(
            id=row.id,
            name=row.name,
            start_date=row.first_day or row.start_date,
            end_date=row.last_day or row.start_date,
            meal_count=row.meal_count,
            created_at=row.created_at,
        )
        for row in db.execute(stmt)
    ]


def get_plan(db: Session, user: CurrentUser, plan_id: int) -> models.MealPlan:
    """One plan with its items and their recipes.

    404 rather than 403 when it belongs to another household: a distinct status
    would confirm that the id exists, which is a membership oracle over a guessable
    integer key.
    """
    stmt = (
        select(models.MealPlan)
        .where(models.MealPlan.id == plan_id)
        .where(visible_to(models.MealPlan, user))
        .options(selectinload(models.MealPlan.items).joinedload(models.MealPlanItem.recipe))
    )
    plan = db.scalars(stmt).first()
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meal plan not found")
    return plan


def delete_plan(db: Session, user: CurrentUser, plan_id: int) -> None:
    """Remove a plan. Its items go with it via cascade; the recipes do not.

    Committing a plan can materialise staged picks into real recipes, and those
    are the household's now -- deleting the plan that introduced them would take
    away recipes someone may have edited or cooked from since.
    """
    plan = get_plan(db, user, plan_id)
    db.delete(plan)
    db.commit()

