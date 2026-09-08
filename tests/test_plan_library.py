"""Reading back saved plans.

Committing was write-only: the plan landed in the database and there was no way
to list it, open it, or remove it. These cover the three that close that loop,
plus the one question the planner tab actually asks -- "what are we eating?" --
which is not the same as "what was saved last".
"""
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.schemas.planner import (
    MealPlanCreate,
    MealPlanItemCreate,
    MealPlanRead,
)
from jarvis_recipes.app.schemas.recipe import RecipeCreate
from jarvis_recipes.app.services import planner_service, recipes_service

HOUSE = "55555555-5555-5555-5555-555555555555"
OTHER_HOUSE = "66666666-6666-6666-6666-666666666666"

TODAY = date.today()


@pytest.fixture
def user():
    return CurrentUser(id=1, household_id=HOUSE)


@pytest.fixture
def housemate():
    """Same household, different person -- must see the same plans."""
    return CurrentUser(id=2, household_id=HOUSE)


@pytest.fixture
def outsider():
    return CurrentUser(id=3, household_id=OTHER_HOUSE)


def _recipe(db, user, title="Beef Stroganoff", minutes=45):
    return recipes_service.create_recipe(
        db,
        user,
        RecipeCreate(
            title=title,
            total_time_minutes=minutes,
            ingredients=[{"text": "1 cup flour"}],
            steps=[{"step_number": 1, "text": "Cook."}],
        ),
    )


def _commit(db, user, days, name=None, recipe=None):
    """Commit a dinner on each of `days`."""
    rec = recipe or _recipe(db, user)
    return planner_service.commit_plan(
        db,
        user,
        MealPlanCreate(
            name=name,
            start_date=min(days),
            items=[
                MealPlanItemCreate(date=d, meal_type="dinner", recipe_id=rec.id) for d in days
            ],
        ),
    )


# ── listing ───────────────────────────────────────────────────────────────────


def test_a_saved_plan_can_be_listed(db_session, user):
    _commit(db_session, user, [TODAY, TODAY + timedelta(days=1)], name="This week")

    plans = planner_service.list_plans(db_session, user)

    assert len(plans) == 1
    assert plans[0].name == "This week"
    assert plans[0].meal_count == 2


def test_the_span_comes_from_the_days_actually_planned(db_session, user):
    # A plan for Mon/Tue/Fri is three days long. start_date on the row says when
    # it begins; only the items say when it ends.
    days = [TODAY, TODAY + timedelta(days=1), TODAY + timedelta(days=4)]
    _commit(db_session, user, days)

    [plan] = planner_service.list_plans(db_session, user)

    assert plan.start_date == days[0]
    assert plan.end_date == days[-1]
    assert plan.meal_count == 3


def test_plans_come_back_newest_first(db_session, user):
    _commit(db_session, user, [TODAY], name="older")
    _commit(db_session, user, [TODAY + timedelta(days=7)], name="newer")

    names = [p.name for p in planner_service.list_plans(db_session, user)]

    assert names == ["newer", "older"]


def test_a_housemate_sees_the_same_plans(db_session, user, housemate):
    _commit(db_session, user, [TODAY], name="Ours")

    assert [p.name for p in planner_service.list_plans(db_session, housemate)] == ["Ours"]


def test_another_household_sees_none_of_them(db_session, user, outsider):
    _commit(db_session, user, [TODAY], name="Ours")

    assert planner_service.list_plans(db_session, outsider) == []


# ── opening one ───────────────────────────────────────────────────────────────


def test_opening_a_plan_returns_its_meals(db_session, user):
    rec = _recipe(db_session, user, title="Thai Basil Chicken", minutes=25)
    saved = _commit(db_session, user, [TODAY], recipe=rec)

    plan = planner_service.get_plan(db_session, user, saved.id)

    assert [i.meal_type for i in plan.items] == ["dinner"]
    assert plan.items[0].recipe.title == "Thai Basil Chicken"


def test_a_plan_serializes_with_its_recipe_titles(db_session, user):
    # The reason this schema exists: without lifting the joined recipe onto the
    # item, a 21-meal plan is 21 extra requests to render, and `title` comes back
    # None with no error to explain it.
    rec = _recipe(db_session, user, title="Thai Basil Chicken", minutes=25)
    saved = _commit(db_session, user, [TODAY], recipe=rec)

    body = MealPlanRead.model_validate(
        planner_service.get_plan(db_session, user, saved.id)
    )

    assert body.items[0].title == "Thai Basil Chicken"
    assert body.items[0].total_time_minutes == 25


def test_another_households_plan_is_not_found_rather_than_forbidden(db_session, user, outsider):
    # 403 would confirm the id exists. Plan ids are small integers.
    saved = _commit(db_session, user, [TODAY])

    with pytest.raises(HTTPException) as err:
        planner_service.get_plan(db_session, outsider, saved.id)

    assert err.value.status_code == 404


# ── deleting ──────────────────────────────────────────────────────────────────


def test_deleting_a_plan_removes_it_and_its_items(db_session, user):
    saved = _commit(db_session, user, [TODAY, TODAY + timedelta(days=1)])

    planner_service.delete_plan(db_session, user, saved.id)

    assert planner_service.list_plans(db_session, user) == []
    with pytest.raises(HTTPException):
        planner_service.get_plan(db_session, user, saved.id)


def test_deleting_a_plan_keeps_the_recipes(db_session, user):
    # Commit materialises staged picks into the box. Those recipes belong to the
    # household now -- someone may have edited or cooked from them since.
    rec = _recipe(db_session, user, title="Keep Me")
    saved = _commit(db_session, user, [TODAY], recipe=rec)

    planner_service.delete_plan(db_session, user, saved.id)

    assert recipes_service.get_recipe(db_session, user, rec.id).title == "Keep Me"


def test_another_household_cannot_delete_a_plan(db_session, user, outsider):
    saved = _commit(db_session, user, [TODAY])

    with pytest.raises(HTTPException) as err:
        planner_service.delete_plan(db_session, outsider, saved.id)

    assert err.value.status_code == 404
    assert len(planner_service.list_plans(db_session, user)) == 1


# ── "what are we eating?" ─────────────────────────────────────────────────────


def test_the_current_plan_is_the_one_starting_soonest(db_session, user):
    # NOT the newest row. Planning next month on Monday must not displace the
    # plan for this week that was committed on Sunday.
    _commit(db_session, user, [TODAY + timedelta(days=1)], name="this week")
    _commit(db_session, user, [TODAY + timedelta(days=30)], name="next month")

    assert planner_service.get_current_plan(db_session, user).name == "this week"


def test_a_plan_still_running_today_is_current(db_session, user):
    _commit(db_session, user, [TODAY - timedelta(days=2), TODAY], name="midweek")

    assert planner_service.get_current_plan(db_session, user).name == "midweek"


def test_a_finished_plan_is_not_offered_as_current(db_session, user):
    _commit(db_session, user, [TODAY - timedelta(days=9), TODAY - timedelta(days=7)], name="last week")

    assert planner_service.get_current_plan(db_session, user) is None


def test_the_current_plan_is_household_wide(db_session, user, housemate, outsider):
    _commit(db_session, user, [TODAY], name="Ours")

    assert planner_service.get_current_plan(db_session, housemate).name == "Ours"
    assert planner_service.get_current_plan(db_session, outsider) is None
