"""Committing a meal plan.

Commit is the moment a proposal becomes household data: the plan is saved, and
anything the planner staged stops being temporary and joins the recipe box.

That last part is not cosmetic. Staged rows expire on a timer, and
meal_plan_items.recipe_id is a foreign key to recipes.id — so a committed plan
literally cannot point at a staged pick. Before stage ids were normalised to
integers it could not even be attempted.
"""
import pytest
from datetime import date

from jarvis_recipes.app.db import models
from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.schemas.planner import MealPlanCreate, MealPlanItemCreate
from jarvis_recipes.app.schemas.recipe import RecipeCreate
from jarvis_recipes.app.services import meal_plan_service, planner_service, recipes_service

HOUSE = "44444444-4444-4444-4444-444444444444"


@pytest.fixture
def user():
    return CurrentUser(id=1, household_id=HOUSE)


def _committed_recipe(db, user, title="Existing"):
    return recipes_service.create_recipe(
        db,
        user,
        RecipeCreate(
            title=title,
            ingredients=[{"text": "1 cup flour"}],
            steps=[{"step_number": 1, "text": "Cook."}],
        ),
    )


def _stage(db, user, title="Staged Pick"):
    return meal_plan_service.create_stage_recipe(
        db,
        str(user.id),
        {
            "title": title,
            "ingredients": [{"text": "2 tbsp butter", "quantity_display": "2", "unit": "tbsp"}],
            "steps": [{"text": "Melt it."}],
            "prep_time_minutes": 5,
            "cook_time_minutes": 10,
        },
        request_id="req",
    )


def test_commits_a_plan_of_existing_recipes(db_session, user):
    recipe = _committed_recipe(db_session, user)

    plan = planner_service.commit_plan(
        db_session,
        user,
        MealPlanCreate(
            start_date=date(2026, 9, 7),
            items=[
                MealPlanItemCreate(
                    date=date(2026, 9, 7), meal_type="dinner", recipe_id=recipe.id
                )
            ],
        ),
    )

    assert plan.id is not None
    assert plan.items[0].recipe_id == recipe.id


def test_the_plan_belongs_to_the_household(db_session, user):
    recipe = _committed_recipe(db_session, user)

    plan = planner_service.commit_plan(
        db_session,
        user,
        MealPlanCreate(
            start_date=date(2026, 9, 7),
            items=[
                MealPlanItemCreate(date=date(2026, 9, 7), meal_type="dinner", recipe_id=recipe.id)
            ],
        ),
    )

    assert plan.household_id == HOUSE
    assert plan.user_id == "1", "authorship is kept alongside household visibility"


def test_a_staged_pick_becomes_a_real_recipe(db_session, user):
    stage_id = _stage(db_session, user, "Poke Bowl")

    plan = planner_service.commit_plan(
        db_session,
        user,
        MealPlanCreate(
            start_date=date(2026, 9, 7),
            items=[
                MealPlanItemCreate(
                    date=date(2026, 9, 7),
                    meal_type="dinner",
                    recipe_id=int(stage_id),
                    source="stage",
                )
            ],
        ),
    )

    committed = db_session.get(models.Recipe, plan.items[0].recipe_id)
    assert committed is not None
    assert committed.title == "Poke Bowl"
    assert committed.household_id == HOUSE, "it joins the household's box"


def test_a_materialised_recipe_keeps_its_ingredients_and_steps(db_session, user):
    """The shopping list aggregates ingredients, so losing them here would make
    a committed plan unshoppable."""
    stage_id = _stage(db_session, user)

    plan = planner_service.commit_plan(
        db_session,
        user,
        MealPlanCreate(
            start_date=date(2026, 9, 7),
            items=[
                MealPlanItemCreate(
                    date=date(2026, 9, 7), meal_type="dinner", recipe_id=int(stage_id), source="stage"
                )
            ],
        ),
    )

    committed = db_session.get(models.Recipe, plan.items[0].recipe_id)
    assert [i.text for i in committed.ingredients] == ["2 tbsp butter"]
    assert committed.ingredients[0].unit == "tbsp"
    assert [s.text for s in committed.steps] == ["Melt it."]


def test_the_same_staged_pick_used_twice_becomes_one_recipe(db_session, user):
    """Leftovers: the same meal on two nights should not duplicate the box."""
    stage_id = _stage(db_session, user, "Big Batch Chili")

    plan = planner_service.commit_plan(
        db_session,
        user,
        MealPlanCreate(
            start_date=date(2026, 9, 7),
            items=[
                MealPlanItemCreate(
                    date=date(2026, 9, 7), meal_type="dinner", recipe_id=int(stage_id), source="stage"
                ),
                MealPlanItemCreate(
                    date=date(2026, 9, 8), meal_type="dinner", recipe_id=int(stage_id), source="stage"
                ),
            ],
        ),
    )

    ids = {item.recipe_id for item in plan.items}
    assert len(ids) == 1
    assert (
        db_session.query(models.Recipe).filter(models.Recipe.title == "Big Batch Chili").count()
        == 1
    )


def test_a_missing_staged_pick_is_a_404_not_a_crash(db_session, user):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        planner_service.commit_plan(
            db_session,
            user,
            MealPlanCreate(
                start_date=date(2026, 9, 7),
                items=[
                    MealPlanItemCreate(
                        date=date(2026, 9, 7), meal_type="dinner", recipe_id=999999, source="stage"
                    )
                ],
            ),
        )

    assert exc.value.status_code == 404


def test_another_users_staged_pick_cannot_be_committed(db_session, user):
    """Staged rows are per-user working state, not household data yet."""
    from fastapi import HTTPException

    other_stage = _stage(db_session, CurrentUser(id=2, household_id=HOUSE), "Theirs")

    with pytest.raises(HTTPException) as exc:
        planner_service.commit_plan(
            db_session,
            user,
            MealPlanCreate(
                start_date=date(2026, 9, 7),
                items=[
                    MealPlanItemCreate(
                        date=date(2026, 9, 7),
                        meal_type="dinner",
                        recipe_id=int(other_stage),
                        source="stage",
                    )
                ],
            ),
        )

    assert exc.value.status_code == 404
