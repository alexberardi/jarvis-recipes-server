"""Turn committed meal plans into a shopping list.

Recomputed on every request rather than stored. The list is a VIEW of the plans
for a date range, so it always reflects the current plan -- re-roll Thursday's
dinner and the next list is simply right. Storing it would duplicate state that
the plan already owns and go stale the moment the plan changed underneath it.

Shopping trips do not align with plan boundaries either: you might plan a week on
Sunday and shop twice during it. So the range is chosen at list time, not
inherited from the plan.

Combining quantities is deliberately conservative. "2 tbsp butter" and "1/4 cup
butter" are the same ingredient in different units, and guessing a conversion is
how a list quietly tells someone to buy the wrong amount. Same unit -> summed;
different units -> listed separately under one heading, so the person can see
both and decide.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from jarvis_recipes.app.db import models
from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.services.scoping import visible_to

# Leading quantity and prep noise, so "1 1/2 lb beef sirloin, sliced thin"
# groups with "beef sirloin" from another recipe.
_PREP_SUFFIX = re.compile(
    r",\s*(?:finely\s+|thinly\s+|roughly\s+)?"
    r"(?:chopped|diced|sliced|minced|grated|shredded|crumbled|cubed|quartered|halved"
    r"|melted|softened|drained|rinsed|peeled|beaten|sifted|leveled|to taste).*$",
    re.I,
)
_LEADING_QTY = re.compile(r"^\s*[\d¼-¾⅐-⅞./\s-]+")

# Stripped after the quantity: the unit lives in its own column, so leaving it in
# the name gives "lb beef sirloin" and "beef sirloin" as two separate lines.
_UNITS = (
    "lbs", "lb", "pounds", "pound", "ounces", "ounce", "oz",
    "cups", "cup", "tablespoons", "tablespoon", "tbsp", "teaspoons", "teaspoon", "tsp",
    "grams", "gram", "g", "kilograms", "kilogram", "kg",
    "milliliters", "milliliter", "ml", "liters", "liter", "l",
    "cloves", "clove", "cans", "can", "bunches", "bunch", "pints", "pint",
    "slices", "slice", "packages", "package", "pkg", "sprigs", "sprig",
    "quarts", "quart", "gallons", "gallon", "sticks", "stick", "pinch", "dash",
)
_LEADING_UNIT = re.compile(rf"^(?:{'|'.join(_UNITS)})\b\.?\s*", re.I)

# Handles the no-comma case: "Salt and black pepper to taste".
_TRAILING_TO_TASTE = re.compile(r"\s+to taste\s*$", re.I)


def normalize_name(text: str) -> str:
    """The grouping key for an ingredient line.

    Strips a leading quantity and a trailing prep clause -- the two things that
    make the same ingredient look different across recipes. Deliberately shallow:
    no stemming, no synonyms. Over-merging ("cream" with "sour cream") is worse
    than under-merging, because a wrong quantity is invisible in the shop while a
    duplicate line is obvious.
    """
    cleaned = _PREP_SUFFIX.sub("", text or "").strip()
    cleaned = _TRAILING_TO_TASTE.sub("", cleaned).strip()
    cleaned = _LEADING_QTY.sub("", cleaned).strip()
    cleaned = _LEADING_UNIT.sub("", cleaned).strip()
    return cleaned.lower() or (text or "").strip().lower()


@dataclass
class Amount:
    unit: str | None
    quantity: Decimal | None = None
    #  Lines whose quantity could not be parsed keep their original text so the
    #  list can still show them rather than dropping them.
    unparsed: list[str] = field(default_factory=list)


@dataclass
class ShoppingItem:
    name: str
    amounts: list[Amount]
    #  Which recipes asked for it, so a surprising line can be traced back.
    recipes: list[str]


def _to_decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def plans_in_range(
    db: Session, user: CurrentUser, start: date, end: date
) -> list[models.MealPlan]:
    """Committed plans with at least one meal inside the range."""
    stmt = (
        select(models.MealPlan)
        .join(models.MealPlanItem)
        .where(
            visible_to(models.MealPlan, user),
            models.MealPlanItem.date >= start,
            models.MealPlanItem.date <= end,
        )
        .options(
            selectinload(models.MealPlan.items)
            .selectinload(models.MealPlanItem.recipe)
            .selectinload(models.Recipe.ingredients)
        )
        .distinct()
    )
    return list(db.scalars(stmt).all())


def build(db: Session, user: CurrentUser, start: date, end: date) -> list[ShoppingItem]:
    """Aggregate every ingredient needed between two dates, inclusive."""
    grouped: dict[str, ShoppingItem] = {}

    for plan in plans_in_range(db, user, start, end):
        for item in plan.items:
            if not (start <= item.date <= end):
                # A plan can straddle the range; only take the meals inside it.
                continue
            recipe = item.recipe
            if recipe is None:
                continue
            for ing in recipe.ingredients:
                key = normalize_name(ing.text)
                entry = grouped.setdefault(
                    key, ShoppingItem(name=key, amounts=[], recipes=[])
                )
                if recipe.title not in entry.recipes:
                    entry.recipes.append(recipe.title)

                qty = _to_decimal(ing.quantity_value)
                unit = (ing.unit or "").strip().lower() or None

                existing = next((a for a in entry.amounts if a.unit == unit), None)
                if existing is None:
                    existing = Amount(unit=unit)
                    entry.amounts.append(existing)

                if qty is None:
                    existing.unparsed.append(ing.text)
                else:
                    existing.quantity = (existing.quantity or Decimal(0)) + qty

    return sorted(grouped.values(), key=lambda i: i.name)
