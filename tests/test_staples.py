"""Staples: what a household always has in.

The whole point is a shorter list, so the risk sits in two places:

  * an ingredient must never be DROPPED from the list for being a staple --
    someone cooking needs to know the recipe calls for salt, even if they are
    not buying any. It is flagged and grouped, not hidden.
  * the CART is the opposite duty: an order should not include what you already
    have, so there staples are excluded outright.

Names are stored as shopping-list keys, so the matching is exact set membership
rather than anything fuzzy -- see staples_service for why that layering runs one
way only.
"""
from datetime import date

import pytest
from fastapi import HTTPException

from jarvis_recipes.app.db import models
from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.schemas.planner import MealPlanCreate, MealPlanItemCreate
from jarvis_recipes.app.schemas.recipe import RecipeCreate
from jarvis_recipes.app.services import (
    grocery_service,
    planner_service,
    recipes_service,
    shopping_list_service,
    staples_service,
)

HOUSE = "99999999-9999-9999-9999-999999999999"
OTHER_HOUSE = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
MONDAY = date(2026, 9, 7)


@pytest.fixture
def user():
    return CurrentUser(id=1, household_id=HOUSE)


@pytest.fixture
def housemate():
    """Same household, different person -- staples are shared."""
    return CurrentUser(id=2, household_id=HOUSE)


@pytest.fixture
def outsider():
    return CurrentUser(id=3, household_id=OTHER_HOUSE)


def _plan_with(db, user, ingredients, day=MONDAY):
    recipe = recipes_service.create_recipe(
        db,
        user,
        RecipeCreate(
            title="Dinner",
            ingredients=[{"text": t} for t in ingredients],
            steps=[{"step_number": 1, "text": "Cook."}],
        ),
    )
    planner_service.commit_plan(
        db,
        user,
        MealPlanCreate(
            start_date=day,
            items=[MealPlanItemCreate(date=day, meal_type="dinner", recipe_id=recipe.id)],
        ),
    )
    return recipe


def _named(items, name):
    return next((i for i in items if i.name == name), None)


# ── storing them ──────────────────────────────────────────────────────────────

class TestAdding:
    def test_a_recipe_line_is_stored_as_the_shopping_list_key(self, db_session, user):
        # What a client has to hand is a recipe line or a list key, not a tidy
        # noun. Storing it verbatim would match nothing.
        staple = staples_service.add_staple(db_session, user, "2 tbsp Olive Oil, divided")
        assert staple.name == "olive oil"

    def test_adding_twice_returns_the_same_row(self, db_session, user):
        first = staples_service.add_staple(db_session, user, "salt")
        second = staples_service.add_staple(db_session, user, "Salt")
        assert first.id == second.id
        assert len(staples_service.list_staples(db_session, user)) == 1

    def test_a_blank_name_is_refused(self, db_session, user):
        with pytest.raises(HTTPException) as exc:
            staples_service.add_staple(db_session, user, "   ")
        assert exc.value.status_code == 422

    def test_a_quantity_only_string_is_kept_verbatim_and_simply_matches_nothing(
        self, db_session, user
    ):
        # normalize_name never returns empty -- it falls back to the original
        # text so the shopping list can never lose a line. So "2 tbsp" is stored
        # as-is rather than rejected. Harmless: no ingredient normalizes to it,
        # so it flags nothing. Asserted so the fallback is not mistaken for a bug.
        staple = staples_service.add_staple(db_session, user, "2 tbsp")
        assert staple.name == "2 tbsp"


class TestSharing:
    def test_a_housemate_sees_the_same_staples(self, db_session, user, housemate):
        staples_service.add_staple(db_session, user, "olive oil")
        assert staples_service.staple_names(db_session, housemate) == {"olive oil"}

    def test_another_household_does_not(self, db_session, user, outsider):
        staples_service.add_staple(db_session, user, "olive oil")
        assert staples_service.staple_names(db_session, outsider) == set()

    def test_adding_what_a_housemate_already_added_makes_no_second_row(
        self, db_session, user, housemate
    ):
        # Dedupe happens on WRITE too: add_staple looks for a visible row first,
        # so a household never accumulates copies through normal use.
        first = staples_service.add_staple(db_session, user, "salt")
        second = staples_service.add_staple(db_session, housemate, "salt")
        assert first.id == second.id

    def test_duplicate_rows_from_the_backfill_ramp_show_once(
        self, db_session, user, housemate
    ):
        """Two rows, same name, same household -- inserted the only way it happens.

        add_staple cannot produce this: it dedupes across the household. But
        scoping.py's migration ramp can. Both members add "salt" while their rows
        are still household_id NULL (each visible only to its author, so neither
        write sees the other), then backfill_household_ids.py stamps the same
        household on both. The read has to collapse them or the client shows
        "salt" twice with no way to explain it.
        """
        db_session.add_all(
            [
                models.Staple(user_id=str(user.id), household_id=HOUSE, name="salt"),
                models.Staple(user_id=str(housemate.id), household_id=HOUSE, name="salt"),
            ]
        )
        db_session.commit()

        assert [s.name for s in staples_service.list_staples(db_session, user)] == ["salt"]


class TestRemoving:
    def test_removing_stops_it_being_a_staple(self, db_session, user):
        staple = staples_service.add_staple(db_session, user, "salt")
        staples_service.remove_staple(db_session, user, staple.id)
        assert staples_service.staple_names(db_session, user) == set()

    def test_removing_clears_every_duplicate_of_that_name(
        self, db_session, user, housemate
    ):
        """One tap has to actually stop it being a staple.

        Duplicates exist only via the backfill ramp (see the test above), but
        when they do, deleting just the id the client sent leaves the other row
        visible -- the ingredient stays grouped away and the tap looks broken.
        """
        mine = models.Staple(user_id=str(user.id), household_id=HOUSE, name="salt")
        theirs = models.Staple(user_id=str(housemate.id), household_id=HOUSE, name="salt")
        db_session.add_all([mine, theirs])
        db_session.commit()

        staples_service.remove_staple(db_session, user, mine.id)

        assert staples_service.staple_names(db_session, user) == set()

    def test_another_households_staple_is_404_not_403(self, db_session, user, outsider):
        staple = staples_service.add_staple(db_session, outsider, "salt")
        with pytest.raises(HTTPException) as exc:
            staples_service.remove_staple(db_session, user, staple.id)
        # 403 would confirm the id exists, which is a membership oracle.
        assert exc.value.status_code == 404

    def test_an_unknown_id_is_404(self, db_session, user):
        with pytest.raises(HTTPException) as exc:
            staples_service.remove_staple(db_session, user, 987654)
        assert exc.value.status_code == 404


# ── the list ──────────────────────────────────────────────────────────────────

class TestTheShoppingList:
    def test_a_staple_is_flagged_but_still_listed(self, db_session, user):
        _plan_with(db_session, user, ["1 tsp salt", "2 chicken breasts"])
        staples_service.add_staple(db_session, user, "salt")

        items = shopping_list_service.build(
            db_session, user, MONDAY, MONDAY,
            staple_names=staples_service.staple_names(db_session, user),
        )

        salt = _named(items, "salt")
        assert salt is not None, "a staple must never be dropped from the list"
        assert salt.is_staple is True

    def test_everything_else_is_not_flagged(self, db_session, user):
        _plan_with(db_session, user, ["1 tsp salt", "2 chicken breasts"])
        staples_service.add_staple(db_session, user, "salt")

        items = shopping_list_service.build(
            db_session, user, MONDAY, MONDAY,
            staple_names=staples_service.staple_names(db_session, user),
        )

        assert _named(items, "chicken breasts").is_staple is False

    def test_nothing_is_flagged_when_the_caller_passes_no_staples(self, db_session, user):
        # build() defaults to no staples so an existing caller keeps its
        # behaviour rather than silently acquiring this feature.
        _plan_with(db_session, user, ["1 tsp salt"])
        staples_service.add_staple(db_session, user, "salt")

        items = shopping_list_service.build(db_session, user, MONDAY, MONDAY)

        assert _named(items, "salt").is_staple is False


# ── the cart ──────────────────────────────────────────────────────────────────

class TestTheCart:
    def test_a_staple_is_left_out_of_the_cart(self, db_session, user):
        _plan_with(db_session, user, ["1 tsp salt", "2 chicken breasts"])
        staples_service.add_staple(db_session, user, "salt")

        cart = grocery_service.build_cart(db_session, user, MONDAY, MONDAY)

        ordered = {i.ingredient_name for i in cart.items} | {
            u.ingredient_name for u in cart.unmatched
        }
        assert "salt" not in ordered, "the cart should not re-buy a staple"
        assert "chicken breasts" in ordered

    def test_the_cart_is_unchanged_when_nothing_is_a_staple(self, db_session, user):
        _plan_with(db_session, user, ["1 tsp salt", "2 chicken breasts"])

        cart = grocery_service.build_cart(db_session, user, MONDAY, MONDAY)

        ordered = {i.ingredient_name for i in cart.items} | {
            u.ingredient_name for u in cart.unmatched
        }
        assert {"salt", "chicken breasts"} <= ordered


# ── over HTTP ─────────────────────────────────────────────────────────────────

class TestRoutes:
    def _auth(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_add_list_and_remove(self, client, user_token):
        created = client.post("/staples", json={"name": "2 tbsp olive oil"},
                              headers=self._auth(user_token))
        assert created.status_code == 201
        assert created.json()["name"] == "olive oil"

        listed = client.get("/staples", headers=self._auth(user_token))
        assert [s["name"] for s in listed.json()] == ["olive oil"]

        gone = client.delete(f"/staples/{created.json()['id']}",
                             headers=self._auth(user_token))
        assert gone.status_code == 204
        assert client.get("/staples", headers=self._auth(user_token)).json() == []

    def test_adding_twice_over_http_leaves_one(self, client, user_token):
        for _ in range(2):
            assert client.post("/staples", json={"name": "salt"},
                               headers=self._auth(user_token)).status_code == 201
        assert len(client.get("/staples", headers=self._auth(user_token)).json()) == 1

    def test_staples_require_a_token(self, client):
        assert client.get("/staples").status_code in (401, 403)

    def test_another_users_staple_cannot_be_removed(self, client, user_token, other_user_token):
        created = client.post("/staples", json={"name": "salt"},
                              headers=self._auth(user_token))
        resp = client.delete(f"/staples/{created.json()['id']}",
                             headers=self._auth(other_user_token))
        assert resp.status_code == 404
