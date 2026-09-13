"""Grouping for the shopping list.

The list exists so you can buy once per ingredient. Before this, three recipes
using ground beef produced three lines:

    ground beef                        1 lb
    lean ground beef                   1.5 lb
    lean ground beef (93/7 or leaner)  1.5 lb

so you buy three times. The culprit was the parenthetical aside, which makes the
same ingredient look different across recipes -- exactly what this function
exists to strip, alongside the leading quantity and the prep clause.

The line it must NOT cross is over-merging: a wrong quantity is invisible in the
shop, a duplicate line is obvious. So an aside is stripped and an identifying
parenthetical is kept.
"""

import pytest

from jarvis_recipes.app.services.shopping_list_service import normalize_name


@pytest.mark.parametrize(
    "written,expected",
    [
        # Asides: an alternative, an optional note, an approximate count, a bare
        # metric, a fat ratio.
        ("lean ground beef (93/7 or leaner)", "lean ground beef"),
        ("lean ground beef (93/7 or 96/4)", "lean ground beef"),
        ("honey (or agave)", "honey"),
        ("honey (optional, balances heat)", "honey"),
        ("sriracha (optional)", "sriracha"),
        ("taco seasoning (or homemade)", "taco seasoning"),
        ("pico de gallo (store-bought or homemade)", "pico de gallo"),
        ("corn (fresh, canned, or frozen)", "corn"),
        ("russet potatoes (about 4 medium)", "russet potatoes"),
        ("sweet potatoes (approx. 600g)", "sweet potatoes"),
        ("hoisin sauce (32g)", "hoisin sauce"),
        ("lemon juice (50 ml)", "lemon juice"),
    ],
)
def test_asides_are_stripped(written, expected):
    assert normalize_name(written) == expected


def test_the_same_ingredient_written_three_ways_merges():
    """The exact failure: three lines for one trip to the butcher."""
    written = [
        "1 1/2 lb lean ground beef (93/7 or leaner)",
        "lean ground beef",
        "1 lb lean ground beef, drained",
    ]
    assert len({normalize_name(w) for w in written}) == 1


@pytest.mark.parametrize(
    "written,expected",
    [
        # A parenthetical that IDENTIFIES the thing is not an aside.
        ("gochujang (korean chili paste)", "gochujang (korean chili paste)"),
        ("greek yogurt (greek yogurt base)", "greek yogurt (greek yogurt base)"),
    ],
)
def test_identifying_parentheticals_are_kept(written, expected):
    assert normalize_name(written) == expected


@pytest.mark.parametrize(
    "a,b",
    [
        # Things a shopper must buy separately.
        ("heavy cream", "sour cream"),
        ("ground beef", "ground turkey"),
        ("sweet potatoes", "russet potatoes"),
        ("chicken breast", "chicken thighs"),
        ("2% milk", "whole milk"),
    ],
)
def test_different_ingredients_do_not_merge(a, b):
    assert normalize_name(a) != normalize_name(b)


@pytest.mark.parametrize(
    "written,expected",
    [
        # A digit glued to what follows is part of the name, not a quantity.
        # "2% milk" grouped under "% milk" before the lookahead was added.
        ("2% milk", "2% milk"),
        ("1% milk", "1% milk"),
        ("2 cups whole milk", "whole milk"),
    ],
)
def test_a_leading_digit_is_only_stripped_when_it_is_a_quantity(written, expected):
    assert normalize_name(written) == expected


@pytest.mark.parametrize(
    "written,expected",
    [
        ("1 1/2 lb beef sirloin, sliced thin", "beef sirloin"),
        ("3 cloves garlic, minced", "garlic"),
        ("Salt and black pepper to taste", "salt and black pepper"),
        ("567 g chicken tenders", "chicken tenders"),
    ],
)
def test_existing_behaviour_is_unchanged(written, expected):
    """The quantity, unit and prep stripping this already did."""
    assert normalize_name(written) == expected
