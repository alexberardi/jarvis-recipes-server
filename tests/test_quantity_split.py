import pytest

from jarvis_recipes.app.services.parse_job_service import _split_qty_unit


@pytest.mark.parametrize(
    "qty,expected_qty,expected_unit",
    [
        ("2 cups", "2", "cups"),
        ("1 1/2 cups", "1 1/2", "cups"),
        ("3/4 teaspoon", "3/4", "teaspoon"),
        ("1 pound (85% lean)", "1", "pound"),
        ("2 green onions, thinly sliced", "2 green onions, thinly sliced", None),
        ("pinch salt", None, "pinch"),
        ("%3 cup", "%3 cup", None),
        ("2 tablespoons.", "2", "tablespoons"),
    ],
)
def test_split_qty_unit(qty, expected_qty, expected_unit):
    qty_out, unit_out = _split_qty_unit(qty)
    assert qty_out == expected_qty
    assert unit_out == expected_unit

