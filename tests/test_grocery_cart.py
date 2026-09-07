"""Exporting a shopping list to a retailer cart.

The property worth defending: the export never invents a SKU. Everything in the
cart came either from a person choosing a product or from the model matching an
ingredient to a product a person already chose. A hallucinated Walmart item id is
a wrong thing in someone's cart, found at the checkout, with no way for the model
to know it got it wrong.
"""
from datetime import date, timedelta

import pytest

from jarvis_recipes.app.db import models
from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.schemas.planner import MealPlanCreate, MealPlanItemCreate
from jarvis_recipes.app.schemas.recipe import RecipeCreate
from jarvis_recipes.app.services import grocery_service, planner_service, recipes_service

HOUSE = "77777777-7777-7777-7777-777777777777"
OTHER_HOUSE = "88888888-8888-8888-8888-888888888888"

MONDAY = date(2026, 9, 7)
SUNDAY = MONDAY + timedelta(days=6)


@pytest.fixture
def user():
    return CurrentUser(id=1, household_id=HOUSE)


@pytest.fixture
def housemate():
    return CurrentUser(id=2, household_id=HOUSE)


@pytest.fixture
def outsider():
    return CurrentUser(id=3, household_id=OTHER_HOUSE)


def _plan_with(db, user, ingredients, day=MONDAY):
    """`ingredients` are (text, quantity_display, unit) as the parser stores them.

    The quantity and unit live in their own columns; a bare text line has no
    parsed amount, which is a different case (covered separately) from the one
    package maths needs.
    """
    recipe = recipes_service.create_recipe(
        db,
        user,
        RecipeCreate(
            title="Dinner",
            ingredients=[
                {"text": t, "quantity_display": q, "unit": u} if q else {"text": t}
                for t, q, u in (
                    i if isinstance(i, tuple) else (i, None, None) for i in ingredients
                )
            ],
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


# ── the map ───────────────────────────────────────────────────────────────────


def test_a_raw_line_reduces_to_the_key_the_shopping_list_uses(db_session, user):
    # The map is keyed on "beef sirloin" so one row covers every recipe's
    # phrasing. Keying on the raw line needs a row per recipe and never hits.
    grocery_service.upsert_mapping(
        db_session,
        user,
        grocery_service.map_key("1 1/2 lb beef sirloin, sliced thin"),
        sku="123",
        product_name="Beef Sirloin",
    )

    [mapping] = grocery_service.list_map(db_session, user)
    assert mapping.ingredient_name == "beef sirloin"


def test_a_key_that_starts_with_digits_is_stored_intact(db_session, user):
    # normalize_name strips a leading quantity, so applying it to an ALREADY
    # normalized key eats the front of these. "90/10 ground beef" would become
    # "ground beef" -- a different product, pointed at silently.
    for name in ("90/10 ground beef", "2% milk", "7up"):
        grocery_service.upsert_mapping(db_session, user, name, sku="x")

    stored = {m.ingredient_name for m in grocery_service.list_map(db_session, user)}
    assert stored == {"90/10 ground beef", "2% milk", "7up"}


def test_a_housemate_shares_the_map(db_session, user, housemate):
    grocery_service.upsert_mapping(db_session, user, "beef sirloin", sku="123")

    assert len(grocery_service.list_map(db_session, housemate)) == 1


def test_another_household_has_its_own_map(db_session, user, outsider):
    grocery_service.upsert_mapping(db_session, user, "beef sirloin", sku="123")

    assert grocery_service.list_map(db_session, outsider) == []


def test_saving_the_same_ingredient_twice_replaces_it(db_session, user):
    grocery_service.upsert_mapping(db_session, user, "beef sirloin", sku="123")
    grocery_service.upsert_mapping(db_session, user, "beef sirloin", sku="456")

    mappings = grocery_service.list_map(db_session, user)
    assert len(mappings) == 1
    assert mappings[0].sku == "456"


def test_a_model_guess_never_overwrites_a_persons_choice(db_session, user):
    # The background pass runs on a stale view of the map. Letting it win would
    # undo a correction made seconds earlier with nothing on screen to explain it.
    grocery_service.upsert_mapping(db_session, user, "ground beef", sku="MINE", source="manual")

    grocery_service.upsert_mapping(db_session, user, "ground beef", sku="GUESS", source="llm")

    assert grocery_service.list_map(db_session, user)[0].sku == "MINE"


def test_a_person_can_overwrite_a_model_guess(db_session, user):
    grocery_service.upsert_mapping(db_session, user, "ground beef", sku="GUESS", source="llm")

    grocery_service.upsert_mapping(db_session, user, "ground beef", sku="MINE", source="manual")

    [mapping] = grocery_service.list_map(db_session, user)
    assert mapping.sku == "MINE"
    assert mapping.source == "manual"


def test_another_household_cannot_delete_a_mapping(db_session, user, outsider):
    mapping = grocery_service.upsert_mapping(db_session, user, "beef sirloin", sku="123")

    assert grocery_service.delete_mapping(db_session, outsider, mapping.id) is False
    assert len(grocery_service.list_map(db_session, user)) == 1


# ── the cart ──────────────────────────────────────────────────────────────────


def test_a_mapped_ingredient_becomes_a_cart_item(db_session, user):
    _plan_with(db_session, user, [("1 lb ground beef", "1", "lb")])
    grocery_service.upsert_mapping(db_session, user, "ground beef", sku="10450114", unit_size="1 lb")

    cart = grocery_service.build_cart(db_session, user, MONDAY, SUNDAY)

    assert [i.sku for i in cart.items] == ["10450114"]
    assert cart.url == "https://affil.walmart.com/cart/addToCart?items=10450114_1"


def test_an_unmapped_ingredient_comes_back_for_the_picker(db_session, user):
    _plan_with(db_session, user, [("1 lb ground beef", "1", "lb"), ("2 cups jasmine rice", "2", "cups")])
    grocery_service.upsert_mapping(db_session, user, "ground beef", sku="10450114")

    cart = grocery_service.build_cart(db_session, user, MONDAY, SUNDAY)

    assert [u.ingredient_name for u in cart.unmatched] == ["jasmine rice"]
    # With the amount, so the picker can search for a product rather than a noun.
    assert "2 cups" in cart.unmatched[0].amount_display


def test_nothing_mapped_means_no_link_rather_than_an_empty_cart(db_session, user):
    _plan_with(db_session, user, [("2 cups jasmine rice", "2", "cups")])

    cart = grocery_service.build_cart(db_session, user, MONDAY, SUNDAY)

    assert cart.url is None
    assert cart.items == []


def test_a_larger_requirement_buys_more_packages(db_session, user):
    # 3 lb of beef against a 1 lb package is three packages. Rounding DOWN sends
    # someone home short, which they discover mid-recipe.
    _plan_with(db_session, user, [("3 lb ground beef", "3", "lb")])
    grocery_service.upsert_mapping(db_session, user, "ground beef", sku="123", unit_size="1 lb")

    cart = grocery_service.build_cart(db_session, user, MONDAY, SUNDAY)

    assert cart.items[0].quantity == 3


def test_a_partial_package_still_buys_one_whole_one(db_session, user):
    _plan_with(db_session, user, [("1.4 lb ground beef", "1.4", "lb")])
    grocery_service.upsert_mapping(db_session, user, "ground beef", sku="123", unit_size="1 lb")

    cart = grocery_service.build_cart(db_session, user, MONDAY, SUNDAY)

    assert cart.items[0].quantity == 2


def test_mismatched_units_buy_one_rather_than_guessing(db_session, user):
    # "3 cloves" against a "2 lb" bag is not a ratio. Pretending it is puts 0 or
    # 40 in the cart; one bag is the answer a person would give.
    _plan_with(db_session, user, [("3 cloves garlic", "3", "cloves")])
    grocery_service.upsert_mapping(db_session, user, "garlic", sku="123", unit_size="2 lb")

    cart = grocery_service.build_cart(db_session, user, MONDAY, SUNDAY)

    assert cart.items[0].quantity == 1


def test_an_unknown_package_size_buys_one(db_session, user):
    _plan_with(db_session, user, [("3 lb ground beef", "3", "lb")])
    grocery_service.upsert_mapping(db_session, user, "ground beef", sku="123", unit_size=None)

    assert grocery_service.build_cart(db_session, user, MONDAY, SUNDAY).items[0].quantity == 1


def test_meals_outside_the_range_are_not_shopped_for(db_session, user):
    _plan_with(db_session, user, [("1 lb ground beef", "1", "lb")], day=MONDAY)
    _plan_with(db_session, user, [("2 cups jasmine rice", "2", "cups")], day=SUNDAY + timedelta(days=7))
    grocery_service.upsert_mapping(db_session, user, "ground beef", sku="123")
    grocery_service.upsert_mapping(db_session, user, "jasmine rice", sku="456")

    cart = grocery_service.build_cart(db_session, user, MONDAY, SUNDAY)

    assert [i.sku for i in cart.items] == ["123"]


def test_another_households_plan_is_not_shopped_for(db_session, user, outsider):
    _plan_with(db_session, user, [("1 lb ground beef", "1", "lb")])

    cart = grocery_service.build_cart(db_session, outsider, MONDAY, SUNDAY)

    assert cart.items == []
    assert cart.unmatched == []


def test_an_unsupported_retailer_is_refused(db_session, user):
    with pytest.raises(ValueError):
        grocery_service.build_cart(db_session, user, MONDAY, SUNDAY, retailer="costco")


# ── the background matching pass ──────────────────────────────────────────────


def test_the_prompt_offers_only_this_households_products(db_session, user, outsider):
    grocery_service.upsert_mapping(db_session, user, "ground beef", sku="MINE", product_name="80/20 Beef")
    grocery_service.upsert_mapping(db_session, outsider, "ground beef", sku="THEIRS")

    messages = grocery_service.build_match_prompt(
        grocery_service.list_map(db_session, user), ["90/10 ground beef"]
    )

    body = messages[-1]["content"]
    assert "80/20 Beef" in body
    assert "THEIRS" not in body


def test_a_match_writes_an_alias_pointing_at_the_same_product(db_session, user):
    saved = grocery_service.upsert_mapping(
        db_session, user, "ground beef", sku="10450114", product_name="80/20 Beef", unit_size="1 lb"
    )

    grocery_service.apply_matches(
        db_session, user, [{"ingredient": "90/10 ground beef", "candidate_id": saved.id}]
    )

    alias = next(
        m for m in grocery_service.list_map(db_session, user) if m.ingredient_name == "90/10 ground beef"
    )
    assert alias.sku == "10450114"
    assert alias.unit_size == "1 lb"
    assert alias.source == "llm"


def test_a_learned_alias_makes_the_next_cart_hit_deterministically(db_session, user):
    _plan_with(db_session, user, [("1 lb 90/10 ground beef", "1", "lb")])
    saved = grocery_service.upsert_mapping(db_session, user, "ground beef", sku="10450114")
    assert grocery_service.build_cart(db_session, user, MONDAY, SUNDAY).unmatched

    grocery_service.apply_matches(
        db_session, user, [{"ingredient": "90/10 ground beef", "candidate_id": saved.id}]
    )

    cart = grocery_service.build_cart(db_session, user, MONDAY, SUNDAY)
    assert [i.sku for i in cart.items] == ["10450114"]
    assert cart.unmatched == []


def test_a_declined_match_writes_nothing(db_session, user):
    # Returning null is the correct answer when nothing in the map is the same
    # grocery item, and must not be turned into a row.
    grocery_service.upsert_mapping(db_session, user, "ground beef", sku="123")

    grocery_service.apply_matches(
        db_session, user, [{"ingredient": "buttermilk", "candidate_id": None}]
    )

    assert [m.ingredient_name for m in grocery_service.list_map(db_session, user)] == ["ground beef"]


def test_a_candidate_id_from_another_household_is_ignored(db_session, user, outsider):
    # The model is asked to pick from a list we gave it. A model returning
    # something else is normal; an unchecked id here is an IDOR that writes
    # another household's SKU into this one's map.
    theirs = grocery_service.upsert_mapping(db_session, outsider, "ground beef", sku="THEIRS")
    grocery_service.upsert_mapping(db_session, user, "flour", sku="MINE")

    grocery_service.apply_matches(
        db_session, user, [{"ingredient": "90/10 ground beef", "candidate_id": theirs.id}]
    )

    assert [m.sku for m in grocery_service.list_map(db_session, user)] == ["MINE"]


def test_a_garbage_response_writes_nothing(db_session, user):
    grocery_service.upsert_mapping(db_session, user, "flour", sku="MINE")

    grocery_service.apply_matches(
        db_session,
        user,
        [{"candidate_id": 1}, {"ingredient": "x"}, {}, {"ingredient": "y", "candidate_id": 99999}],
    )

    assert len(grocery_service.list_map(db_session, user)) == 1


def test_an_empty_map_queues_nothing(db_session, user):
    # With no candidates the model can only invent, which is the one thing it
    # must never do. The first few products have to come from the person.
    assert grocery_service.enqueue_match_pass(db_session, user, ["ground beef"]) is None
