from decimal import Decimal

from jarvis_recipes.app.services.quantity_parser import parse_quantity_display


def test_parse_quantity_valid():
    assert parse_quantity_display("1") == Decimal("1")
    assert parse_quantity_display("0.5") == Decimal("0.5")
    assert parse_quantity_display("1/2") == Decimal("0.5")
    assert parse_quantity_display("1 1/2") == Decimal("1.5")


def test_parse_quantity_invalid():
    assert parse_quantity_display(None) is None
    assert parse_quantity_display("") is None
    assert parse_quantity_display("   ") is None
    assert parse_quantity_display("1/0") is None
    assert parse_quantity_display("abc") is None

