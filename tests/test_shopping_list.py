"""Building a shopping list from committed plans.

The list is recomputed from the plan every time, so the tests are about
aggregation rather than persistence. Two behaviours carry the most risk:

  * merging the same ingredient written differently across recipes
  * NOT merging quantities in different units, because guessing a conversion is
    how a list quietly tells someone to buy the wrong amount
"""
from datetime import date

import pytest

from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.schemas.planner import MealPlanCreate, MealPlanItemCreate
from jarvis_recipes.app.schemas.recipe import RecipeCreate
from jarvis_recipes.app.services import planner_service, recipes_service, shopping_list_service

HOUSE = "55555555-5555-5555-5555-555555555555"


@pytest.fixture
def user():
    return CurrentUser(id=1, household_id=HOUSE)


def _recipe(db, user, title, ingredients):
    return recipes_service.create_recipe(
        db,
        user,
        RecipeCreate(
            title=title,
            ingredients=ingredients,
            steps=[{"step_number": 1, "text": "Cook."}],
        ),
    )


def _plan(db, user, when, recipe, meal="dinner"):
    return planner_service.commit_plan(
        db,
        user,
        MealPlanCreate(
            start_date=when,
            items=[MealPlanItemCreate(date=when, meal_type=meal, recipe_id=recipe.id)],
        ),
    )


def _named(items, name):
    return next((i for i in items if i.name == name), None)


class TestNormalisation:
    def test_quantity_unit_and_prep_are_stripped(self):
        assert shopping_list_service.normalize_name("1 1/2 lb beef sirloin, sliced thin") == (
            "beef sirloin"
        )

    def test_the_same_ingredient_written_two_ways_groups(self):
        a = shopping_list_service.normalize_name("8 oz cremini mushrooms, quartered")
        b = shopping_list_service.normalize_name("cremini mushrooms")
        assert a == b

    def test_distinct_ingredients_stay_distinct(self):
        """Over-merging is worse than under-merging: a wrong quantity is invisible
        in the shop, a duplicate line is obvious."""
        assert shopping_list_service.normalize_name(
            "1 cup sour cream"
        ) != shopping_list_service.normalize_name("1 cup heavy cream")


class TestAggregation:
    def test_same_unit_quantities_are_summed(self, db_session, user):
        a = _recipe(db_session, user, "A", [{"text": "2 tbsp butter", "quantity_display": "2", "unit": "tbsp"}])
        b = _recipe(db_session, user, "B", [{"text": "3 tbsp butter", "quantity_display": "3", "unit": "tbsp"}])
        _plan(db_session, user, date(2026, 9, 7), a)
        _plan(db_session, user, date(2026, 9, 8), b)

        items = shopping_list_service.build(db_session, user, date(2026, 9, 7), date(2026, 9, 8))

        butter = _named(items, "butter")
        assert butter is not None
        assert [(float(x.quantity), x.unit) for x in butter.amounts] == [(5.0, "tbsp")]

    def test_different_units_are_listed_separately_not_converted(self, db_session, user):
        """"2 tbsp" and "1/4 cup" are the same thing in different units. Guessing
        a conversion risks a silently wrong amount, so both are shown."""
        a = _recipe(db_session, user, "A", [{"text": "2 tbsp butter", "quantity_display": "2", "unit": "tbsp"}])
        b = _recipe(db_session, user, "B", [{"text": "1/4 cup butter", "quantity_display": "1/4", "unit": "cup"}])
        _plan(db_session, user, date(2026, 9, 7), a)
        _plan(db_session, user, date(2026, 9, 8), b)

        items = shopping_list_service.build(db_session, user, date(2026, 9, 7), date(2026, 9, 8))

        units = {x.unit for x in _named(items, "butter").amounts}
        assert units == {"tbsp", "cup"}

    def test_an_unparseable_quantity_is_kept_not_dropped(self, db_session, user):
        """"Salt and pepper to taste" still belongs on the list."""
        a = _recipe(db_session, user, "A", [{"text": "Salt and black pepper to taste"}])
        _plan(db_session, user, date(2026, 9, 7), a)

        items = shopping_list_service.build(db_session, user, date(2026, 9, 7), date(2026, 9, 7))

        entry = _named(items, "salt and black pepper")
        assert entry is not None
        assert entry.amounts[0].unparsed == ["Salt and black pepper to taste"]

    def test_every_contributing_recipe_is_named(self, db_session, user):
        """So a surprising quantity can be traced back to what asked for it."""
        a = _recipe(db_session, user, "Stroganoff", [{"text": "2 tbsp butter", "quantity_display": "2", "unit": "tbsp"}])
        b = _recipe(db_session, user, "Risotto", [{"text": "4 tbsp butter", "quantity_display": "4", "unit": "tbsp"}])
        _plan(db_session, user, date(2026, 9, 7), a)
        _plan(db_session, user, date(2026, 9, 8), b)

        items = shopping_list_service.build(db_session, user, date(2026, 9, 7), date(2026, 9, 8))

        assert sorted(_named(items, "butter").recipes) == ["Risotto", "Stroganoff"]


class TestRange:
    def test_only_meals_inside_the_range_count(self, db_session, user):
        """Shopping trips do not align with plan boundaries -- a plan can straddle
        the range and only the days being shopped for should contribute."""
        inside = _recipe(db_session, user, "Inside", [{"text": "1 cup rice", "quantity_display": "1", "unit": "cup"}])
        outside = _recipe(db_session, user, "Outside", [{"text": "1 cup beans", "quantity_display": "1", "unit": "cup"}])
        planner_service.commit_plan(
            db_session,
            user,
            MealPlanCreate(
                start_date=date(2026, 9, 7),
                items=[
                    MealPlanItemCreate(date=date(2026, 9, 7), meal_type="dinner", recipe_id=inside.id),
                    MealPlanItemCreate(date=date(2026, 9, 20), meal_type="dinner", recipe_id=outside.id),
                ],
            ),
        )

        items = shopping_list_service.build(db_session, user, date(2026, 9, 7), date(2026, 9, 8))

        names = {i.name for i in items}
        assert "rice" in names
        assert "beans" not in names

    def test_an_empty_range_yields_an_empty_list(self, db_session, user):
        assert shopping_list_service.build(db_session, user, date(2030, 1, 1), date(2030, 1, 7)) == []


class TestScoping:
    def test_another_households_plan_is_not_shopped_for(self, db_session, user):
        other = CurrentUser(id=9, household_id="other-house")
        theirs = _recipe(db_session, other, "Theirs", [{"text": "1 cup saffron", "quantity_display": "1", "unit": "cup"}])
        _plan(db_session, other, date(2026, 9, 7), theirs)

        items = shopping_list_service.build(db_session, user, date(2026, 9, 7), date(2026, 9, 7))

        assert "saffron" not in {i.name for i in items}

    def test_a_household_members_plan_is_included(self, db_session, user):
        """One household, one shopping trip."""
        partner = CurrentUser(id=2, household_id=HOUSE)
        hers = _recipe(db_session, partner, "Hers", [{"text": "2 cups flour", "quantity_display": "2", "unit": "cup"}])
        _plan(db_session, partner, date(2026, 9, 7), hers)

        items = shopping_list_service.build(db_session, user, date(2026, 9, 7), date(2026, 9, 7))

        assert "flour" in {i.name for i in items}
