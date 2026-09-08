"""Random meal-plan selection and re-roll.

This is the default planning path: instant, no job, no polling. The tests worth
having are about the awkward cases, because the happy path is a shuffle:

  * a box smaller than the plan (very common early on)
  * a re-roll that must not return what was just rejected
  * meal-type tags treated as a preference, not a filter -- most real recipes are
    untagged, and a strict filter would empty every dinner slot in a box full of
    dinners
  * household scoping, since the box is the family's
"""
from datetime import date

import pytest

from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.schemas.recipe import RecipeCreate
from jarvis_recipes.app.services import random_plan_service, recipes_service
from jarvis_recipes.app.services.random_plan_service import SlotRequest

HOUSE = "33333333-3333-3333-3333-333333333333"


def _make(db, user, title, tags=()):
    return recipes_service.create_recipe(
        db,
        user,
        RecipeCreate(
            title=title,
            ingredients=[{"text": "1 cup flour"}],
            steps=[{"step_number": 1, "text": "Cook."}],
            tags=list(tags),
        ),
    )


@pytest.fixture
def user():
    return CurrentUser(id=1, household_id=HOUSE)


def test_picks_from_the_box(db_session, user):
    for i in range(5):
        _make(db_session, user, f"Dinner {i}", ["dinner"])

    picked = random_plan_service.pick_one(db_session, user, "dinner")

    assert picked is not None
    assert picked.title.startswith("Dinner")


def test_a_plan_never_repeats_a_recipe(db_session, user):
    for i in range(6):
        _make(db_session, user, f"Meal {i}", ["dinner"])
    slots = [SlotRequest(date=date(2026, 9, 7), meal_type="dinner") for _ in range(6)]

    filled = random_plan_service.pick_plan(db_session, user, slots)

    ids = [r.id for _, r in filled if r]
    assert len(ids) == 6
    assert len(set(ids)) == 6, "a plan must not serve the same recipe twice"


def test_a_box_smaller_than_the_plan_leaves_empty_slots(db_session, user):
    """Normal early on: 2 recipes, 5 meals. Empty beats raising."""
    _make(db_session, user, "Only One", ["dinner"])
    _make(db_session, user, "Only Two", ["dinner"])
    slots = [SlotRequest(date=date(2026, 9, 7), meal_type="dinner") for _ in range(5)]

    filled = random_plan_service.pick_plan(db_session, user, slots)

    assert len(filled) == 5
    assert sum(1 for _, r in filled if r is not None) == 2
    assert sum(1 for _, r in filled if r is None) == 3


def test_reroll_does_not_return_the_rejected_recipe(db_session, user):
    a = _make(db_session, user, "Rejected", ["dinner"])
    _make(db_session, user, "The Other One", ["dinner"])

    for _ in range(10):  # it is random; prove it holds every time
        swapped = random_plan_service.pick_one(db_session, user, "dinner", exclude_ids=[a.id])
        assert swapped is not None
        assert swapped.id != a.id


def test_reroll_returns_nothing_when_the_box_is_exhausted(db_session, user):
    only = _make(db_session, user, "The Only Recipe", ["dinner"])

    assert random_plan_service.pick_one(db_session, user, "dinner", exclude_ids=[only.id]) is None


def test_meal_type_is_a_preference_not_a_filter(db_session, user):
    """An untagged box must still fill a dinner slot.

    Tagging is manual, so most real recipes have no meal tag. Filtering strictly
    would return an empty dinner for a box full of usable dinners.
    """
    _make(db_session, user, "Untagged Casserole")

    picked = random_plan_service.pick_one(db_session, user, "dinner")

    assert picked is not None
    assert picked.title == "Untagged Casserole"


def test_tagged_matches_are_preferred_over_untagged(db_session, user):
    _make(db_session, user, "Actual Breakfast", ["breakfast"])
    for i in range(8):
        _make(db_session, user, f"Untagged {i}")

    # With one tagged match, every draw should be it -- not a 1-in-9 shot.
    for _ in range(8):
        assert random_plan_service.pick_one(db_session, user, "breakfast").title == "Actual Breakfast"


def test_selection_is_scoped_to_the_household(db_session, user):
    _make(db_session, CurrentUser(id=9, household_id="other-house"), "Not Our Dinner", ["dinner"])
    _make(db_session, user, "Our Dinner", ["dinner"])

    for _ in range(8):
        assert random_plan_service.pick_one(db_session, user, "dinner").title == "Our Dinner"


def test_a_household_member_can_draw_anothers_recipe(db_session, user):
    """The whole point of the shared box."""
    _make(db_session, CurrentUser(id=2, household_id=HOUSE), "Her Recipe", ["dinner"])

    assert random_plan_service.pick_one(db_session, user, "dinner").title == "Her Recipe"


def test_selection_actually_varies(db_session, user):
    """A shuffle that always returns the same row is indistinguishable from a
    broken one until someone notices their plan never changes."""
    for i in range(10):
        _make(db_session, user, f"Varied {i}", ["dinner"])

    seen = {random_plan_service.pick_one(db_session, user, "dinner").id for _ in range(25)}

    assert len(seen) > 1, "selection is not random"


# ── Route-level ───────────────────────────────────────────────────────────────
# The service tests above all passed while POST /meal-plans/random/reroll was
# returning a 500: a re-roll has no date to echo back, and the response model
# required one. Service coverage does not exercise serialization, so the routes
# get their own tests.


def test_random_plan_route_returns_filled_slots(client, db_session, user_token):
    for i in range(4):
        _make(db_session, CurrentUser(id=1), f"Route Dinner {i}", ["dinner"])

    res = client.post(
        "/meal-plans/random",
        json={"slots": [{"date": "2026-09-07", "meal_type": "dinner"}]},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["incomplete"] is False
    assert body["slots"][0]["recipe_id"] is not None


def test_reroll_route_serializes_without_a_date(client, db_session, user_token):
    """Regression: this returned 500 because `date` was a required field."""
    for i in range(3):
        _make(db_session, CurrentUser(id=1), f"Reroll Dinner {i}", ["dinner"])

    res = client.post(
        "/meal-plans/random/reroll",
        json={"meal_type": "dinner", "exclude_recipe_ids": []},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    assert res.status_code == 200, res.text
    assert res.json()["recipe_id"] is not None


def test_reroll_route_409s_when_nothing_is_left(client, db_session, user_token):
    only = _make(db_session, CurrentUser(id=1), "The Last Recipe", ["dinner"])

    res = client.post(
        "/meal-plans/random/reroll",
        json={"meal_type": "dinner", "exclude_recipe_ids": [only.id]},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    assert res.status_code == 409


# ── Slot tags on re-roll ──────────────────────────────────────────────────────
# Advanced plans configure tags per slot. A re-roll that ignored them would swap
# a "quick" lunch for a three-hour braise, which is worse than not offering the
# button at all.


def test_reroll_prefers_the_slot_tags(db_session, user):
    _make(db_session, user, "Quick One", ["dinner", "quick"])
    for i in range(6):
        _make(db_session, user, f"Slow {i}", ["dinner"])

    for _ in range(8):
        picked = random_plan_service.pick_one(db_session, user, "dinner", tags=["quick"])
        assert picked.title == "Quick One"


def test_slot_tags_beat_the_meal_type(db_session, user):
    """A slot tagged "breakfast" inside a dinner slot means the person wants
    breakfast-for-dinner, not that we should ignore them."""
    _make(db_session, user, "Pancakes For Dinner", ["breakfast"])
    _make(db_session, user, "Ordinary Dinner", ["dinner"])

    for _ in range(8):
        picked = random_plan_service.pick_one(
            db_session, user, "dinner", tags=["breakfast"]
        )
        assert picked.title == "Pancakes For Dinner"


def test_unmatchable_slot_tags_relax_to_the_meal_type(db_session, user):
    """Tags stay a preference. A tag nothing carries must not empty the slot."""
    _make(db_session, user, "A Dinner", ["dinner"])

    picked = random_plan_service.pick_one(
        db_session, user, "dinner", tags=["nonexistent-tag"]
    )

    assert picked is not None
    assert picked.title == "A Dinner"


def test_reroll_route_accepts_tags(client, db_session, user_token):
    _make(db_session, CurrentUser(id=1), "Tagged Quick", ["dinner", "quick"])
    _make(db_session, CurrentUser(id=1), "Untagged Slow", ["dinner"])

    res = client.post(
        "/meal-plans/random/reroll",
        json={"meal_type": "dinner", "exclude_recipe_ids": [], "tags": ["quick"]},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    assert res.status_code == 200, res.text
    assert res.json()["title"] == "Tagged Quick"
