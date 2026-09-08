"""Who can see which recipes.

A recipe box belongs to a household, not a person: a family plans from one
shared box. These pin the four cases that matter, because getting any of them
wrong is either a data leak or a recipe that silently vanishes from someone's
list.

The NULL-household case is the migration ramp and deserves the most attention.
This service cannot resolve a user's household on its own -- that lives in
jarvis-auth -- so rows created before the backfill carry household_id = NULL. If
those stopped being visible, deploying the migration would empty everyone's
recipe box until a separate script finished running.
"""
import pytest

from jarvis_recipes.app.db import models
from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.schemas.recipe import RecipeCreate
from jarvis_recipes.app.services import recipes_service

from conftest import make_token

HOUSE_A = "11111111-1111-1111-1111-111111111111"
HOUSE_B = "22222222-2222-2222-2222-222222222222"


def _payload(title: str) -> RecipeCreate:
    return RecipeCreate(
        title=title,
        ingredients=[{"text": "1 cup flour"}],
        steps=[{"step_number": 1, "text": "Mix."}],
    )


def _titles(client, token) -> set[str]:
    res = client.get("/recipes", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    return {r["title"] for r in res.json()}


def test_household_members_share_a_recipe_box(client, db_session, auth_settings):
    """The whole point: she imports it, he sees it."""
    recipes_service.create_recipe(
        db_session, CurrentUser(id=2, household_id=HOUSE_A), _payload("Her Import")
    )
    token = make_token(1, "u1@example.com", auth_settings, household_id=HOUSE_A)

    assert "Her Import" in _titles(client, token)


def test_other_households_are_not_visible(client, db_session, auth_settings):
    recipes_service.create_recipe(
        db_session, CurrentUser(id=9, household_id=HOUSE_B), _payload("Someone Elses Dinner")
    )
    token = make_token(1, "u1@example.com", auth_settings, household_id=HOUSE_A)

    assert "Someone Elses Dinner" not in _titles(client, token)


def test_a_user_with_no_household_sees_only_their_own(client, db_session, auth_settings):
    """Unchanged from the pre-household behaviour."""
    recipes_service.create_recipe(db_session, CurrentUser(id=2), _payload("Not Mine"))
    recipes_service.create_recipe(db_session, CurrentUser(id=1), _payload("Mine"))
    token = make_token(1, "u1@example.com", auth_settings)

    titles = _titles(client, token)
    assert "Mine" in titles
    assert "Not Mine" not in titles


def test_unbackfilled_rows_stay_visible_to_their_author(client, db_session, auth_settings):
    """The migration ramp.

    A recipe created before the backfill has household_id = NULL. Its author must
    still see it once they have a household, or deploying the migration empties
    the box until a separate script catches up.
    """
    recipes_service.create_recipe(db_session, CurrentUser(id=1), _payload("Legacy Recipe"))
    legacy = (
        db_session.query(models.Recipe).filter(models.Recipe.title == "Legacy Recipe").one()
    )
    assert legacy.household_id is None, "no household on the token means no household stamped"

    token = make_token(1, "u1@example.com", auth_settings, household_id=HOUSE_A)

    assert "Legacy Recipe" in _titles(client, token)


def test_unbackfilled_rows_are_not_visible_to_the_rest_of_the_household(
    client, db_session, auth_settings
):
    """The other half of the ramp: NULL is not a wildcard.

    Until the backfill assigns a household, an un-backfilled row belongs to its
    author alone. Treating NULL as "everyone" would leak across households the
    moment two people shared one.
    """
    recipes_service.create_recipe(db_session, CurrentUser(id=2), _payload("Legacy Of User Two"))
    token = make_token(1, "u1@example.com", auth_settings, household_id=HOUSE_A)

    assert "Legacy Of User Two" not in _titles(client, token)


def test_new_recipes_are_stamped_with_the_household(db_session):
    recipe = recipes_service.create_recipe(
        db_session, CurrentUser(id=1, household_id=HOUSE_A), _payload("Stamped")
    )

    assert recipe.household_id == HOUSE_A
    assert recipe.user_id == "1", "authorship is retained, not replaced"


def test_a_household_member_can_open_anothers_recipe(db_session):
    created = recipes_service.create_recipe(
        db_session, CurrentUser(id=2, household_id=HOUSE_A), _payload("Shared Detail")
    )

    fetched = recipes_service.get_recipe(
        db_session, CurrentUser(id=1, household_id=HOUSE_A), created.id
    )
    assert fetched.id == created.id


def test_tags_reflect_the_household_box(db_session):
    """Tag list is built by joining recipes, so it inherits the scope."""
    recipe = recipes_service.create_recipe(
        db_session, CurrentUser(id=2, household_id=HOUSE_A), _payload("Tagged By Her")
    )
    recipe.tags.append(recipes_service.create_tag(db_session, "weeknight"))
    db_session.commit()

    names = {
        t.name
        for t in recipes_service.list_tags_for_user(
            db_session, CurrentUser(id=1, household_id=HOUSE_A)
        )
    }
    assert "weeknight" in names
