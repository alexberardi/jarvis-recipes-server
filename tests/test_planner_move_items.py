"""Rearranging a saved plan.

Before this, the only way to change a committed plan was to delete it and
commit a new one -- which churns the plan id, drops the plan out of "what are we
eating" mid-edit, and would re-materialise staged recipes that the first commit
had already turned into real ones. So: move the items in place.
"""

from datetime import date, timedelta

import pytest

from jarvis_recipes.app.db import models


def _make_recipe(db, title: str, user_id: str = "1") -> models.Recipe:
    recipe = models.Recipe(
        user_id=user_id, title=title, source_type=models.SourceType.MANUAL, servings=4
    )
    db.add(recipe)
    db.flush()
    return recipe


def _make_plan(db, user_id: str = "1", household_id: str | None = "hh-1"):
    """A three-day plan, one dinner a day."""
    if db.get(models.User, user_id) is None:
        db.add(models.User(user_id=user_id))
        db.flush()
    start = date(2026, 9, 14)
    plan = models.MealPlan(
        user_id=user_id, household_id=household_id, name="Week", start_date=start
    )
    for offset, title in enumerate(["Tacos", "Steak and Fries", "Crockpot Chili"]):
        plan.items.append(
            models.MealPlanItem(
                recipe_id=_make_recipe(db, title, user_id).id,
                date=start + timedelta(days=offset),
                meal_type="dinner",
            )
        )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def _auth(client, token):
    return {"Authorization": f"Bearer {token}"}


def test_moving_a_meal_to_a_free_day(client, db_session, user_token):
    plan = _make_plan(db_session)
    item = plan.items[0]
    target = date(2026, 9, 20)

    res = client.patch(
        f"/planner/plans/{plan.id}/items",
        json={"moves": [{"item_id": item.id, "date": str(target), "meal_type": "dinner"}]},
        headers=_auth(client, user_token),
    )

    assert res.status_code == 200, res.text
    moved = next(i for i in res.json()["items"] if i["id"] == item.id)
    assert moved["date"] == str(target)


def test_moving_onto_an_occupied_day_swaps_them(client, db_session, user_token):
    """"Move it to Thursday" plainly means a swap when Thursday is taken.

    Failing instead would be pedantry, and leaving both meals on one day would
    quietly corrupt the plan.
    """
    plan = _make_plan(db_session)
    first, second = plan.items[0], plan.items[1]
    first_date, second_date = first.date, second.date

    res = client.patch(
        f"/planner/plans/{plan.id}/items",
        json={"moves": [{"item_id": first.id, "date": str(second_date), "meal_type": "dinner"}]},
        headers=_auth(client, user_token),
    )

    assert res.status_code == 200, res.text
    items = {i["id"]: i for i in res.json()["items"]}
    assert items[first.id]["date"] == str(second_date)
    assert items[second.id]["date"] == str(first_date), "the occupant was not displaced"


def test_a_swap_sent_as_two_moves_is_applied_atomically(client, db_session, user_token):
    plan = _make_plan(db_session)
    first, second = plan.items[0], plan.items[1]
    first_date, second_date = first.date, second.date

    res = client.patch(
        f"/planner/plans/{plan.id}/items",
        json={
            "moves": [
                {"item_id": first.id, "date": str(second_date), "meal_type": "dinner"},
                {"item_id": second.id, "date": str(first_date), "meal_type": "dinner"},
            ]
        },
        headers=_auth(client, user_token),
    )

    assert res.status_code == 200, res.text
    items = {i["id"]: i for i in res.json()["items"]}
    assert items[first.id]["date"] == str(second_date)
    assert items[second.id]["date"] == str(first_date)


def test_two_moves_to_the_same_slot_are_rejected(client, db_session, user_token):
    """The client contradicting itself. Guessing a winner would hide the bug."""
    plan = _make_plan(db_session)
    first, second = plan.items[0], plan.items[1]
    target = date(2026, 9, 25)

    res = client.patch(
        f"/planner/plans/{plan.id}/items",
        json={
            "moves": [
                {"item_id": first.id, "date": str(target), "meal_type": "dinner"},
                {"item_id": second.id, "date": str(target), "meal_type": "dinner"},
            ]
        },
        headers=_auth(client, user_token),
    )

    assert res.status_code == 409
    db_session.refresh(plan)
    assert {i.date for i in plan.items} == {
        date(2026, 9, 14),
        date(2026, 9, 15),
        date(2026, 9, 16),
    }, "a rejected request still changed the plan"


def test_changing_the_meal_type(client, db_session, user_token):
    plan = _make_plan(db_session)
    item = plan.items[0]

    res = client.patch(
        f"/planner/plans/{plan.id}/items",
        json={"moves": [{"item_id": item.id, "date": str(item.date), "meal_type": "lunch"}]},
        headers=_auth(client, user_token),
    )

    assert res.status_code == 200, res.text
    moved = next(i for i in res.json()["items"] if i["id"] == item.id)
    assert moved["meal_type"] == "lunch"


def test_start_date_follows_the_earliest_meal(client, db_session, user_token):
    """start_date is stored but derived.

    "What are we eating" and the shopping list both read it, so leaving it
    pointing at a day the plan no longer covers would hide the plan from the
    screen that exists to show it.
    """
    plan = _make_plan(db_session)
    item = plan.items[2]
    earlier = date(2026, 9, 10)

    res = client.patch(
        f"/planner/plans/{plan.id}/items",
        json={"moves": [{"item_id": item.id, "date": str(earlier), "meal_type": "dinner"}]},
        headers=_auth(client, user_token),
    )

    assert res.status_code == 200, res.text
    assert res.json()["start_date"] == str(earlier)


def test_start_date_moves_forward_too(client, db_session, user_token):
    plan = _make_plan(db_session)
    first = plan.items[0]

    res = client.patch(
        f"/planner/plans/{plan.id}/items",
        json={"moves": [{"item_id": first.id, "date": "2026-09-30", "meal_type": "dinner"}]},
        headers=_auth(client, user_token),
    )

    assert res.status_code == 200, res.text
    # The earliest remaining meal is now the second day.
    assert res.json()["start_date"] == "2026-09-15"


def test_an_item_from_another_plan_is_not_found(client, db_session, user_token):
    """404, not 400: naming the reason would confirm the id exists."""
    plan = _make_plan(db_session)
    other = _make_plan(db_session)

    res = client.patch(
        f"/planner/plans/{plan.id}/items",
        json={
            "moves": [
                {"item_id": other.items[0].id, "date": "2026-09-18", "meal_type": "dinner"}
            ]
        },
        headers=_auth(client, user_token),
    )

    assert res.status_code == 404


def test_another_households_plan_is_not_found(client, db_session, other_user_token):
    plan = _make_plan(db_session, user_id="1", household_id="hh-1")

    res = client.patch(
        f"/planner/plans/{plan.id}/items",
        json={"moves": [{"item_id": plan.items[0].id, "date": "2026-09-18", "meal_type": "dinner"}]},
        headers=_auth(client, other_user_token),
    )

    assert res.status_code == 404


def test_no_moves_is_a_no_op(client, db_session, user_token):
    plan = _make_plan(db_session)

    res = client.patch(
        f"/planner/plans/{plan.id}/items",
        json={"moves": []},
        headers=_auth(client, user_token),
    )

    assert res.status_code == 200, res.text
    assert len(res.json()["items"]) == 3


def test_the_request_needs_a_token(client, db_session):
    plan = _make_plan(db_session)

    res = client.patch(
        f"/planner/plans/{plan.id}/items",
        json={"moves": [{"item_id": plan.items[0].id, "date": "2026-09-18", "meal_type": "dinner"}]},
    )

    assert res.status_code in (401, 403)
