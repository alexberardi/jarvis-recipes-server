"""Pick recipes at random from the household's box.

The product this serves is a weekly meal plan its owner actively dislikes doing.
The ask was not "choose well for me" -- it was "give me something, and let me
re-roll the ones I don't fancy". That is a different shape from the LLM planner:

  * it is instant, so there is no job, no polling and no progress screen
  * re-rolling one slot costs one query, where an LLM plan costs another job
  * it is predictable, which is what keeps the person in control

The LLM path still exists behind Advanced for "suggest something thoughtful".

Meal-type matching is a PREFERENCE, not a filter. Recipes are tagged by hand, so
most boxes have plenty of untagged ones; a strict filter would return an empty
dinner slot for a box full of perfectly good dinners. Tagged matches are drawn
first, then anything else fills the gap.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jarvis_recipes.app.db import models
from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.services.scoping import visible_to


@dataclass(frozen=True)
class SlotRequest:
    date: str
    meal_type: str


def _candidates(
    db: Session,
    user: CurrentUser,
    meal_type: str | None,
    exclude_ids: Sequence[int],
    limit: int,
    tagged_only: bool,
    tags: Sequence[str] = (),
):
    stmt = select(models.Recipe).where(visible_to(models.Recipe, user))
    if exclude_ids:
        stmt = stmt.where(models.Recipe.id.notin_(exclude_ids))
    if tagged_only:
        # Explicit slot tags win over the meal type: a slot tagged "quick" is a
        # stronger statement of intent than the fact that it is dinner. Matched
        # case-insensitively -- tags arrive from manual entry, schema.org imports
        # and the stock set, so case is not something either side can rely on.
        wanted = [t.lower() for t in tags] or ([meal_type.lower()] if meal_type else [])
        if wanted:
            stmt = stmt.where(
                models.Recipe.tags.any(func.lower(models.Tag.name).in_(wanted))
            )
    # ORDER BY random() is fine at this size -- a household recipe box is
    # hundreds of rows, not millions. Revisit if that stops being true.
    stmt = stmt.order_by(func.random()).limit(limit)
    return list(db.scalars(stmt).all())


def pick_one(
    db: Session,
    user: CurrentUser,
    meal_type: str | None = None,
    exclude_ids: Iterable[int] = (),
    tags: Sequence[str] = (),
) -> models.Recipe | None:
    """One recipe for one slot. This is the re-roll.

    `exclude_ids` is what the plan already holds, so a re-roll cannot hand back
    the recipe the person just rejected, nor duplicate another slot.
    """
    exclude = list(exclude_ids)
    found = _candidates(db, user, meal_type, exclude, limit=1, tagged_only=True, tags=tags)
    if not found and tags:
        # Slot tags found nothing; try the meal type alone before giving up.
        found = _candidates(db, user, meal_type, exclude, limit=1, tagged_only=True)
    if not found:
        # Nothing tagged at all -- fall back to the whole box rather than
        # returning an empty slot.
        found = _candidates(db, user, meal_type, exclude, limit=1, tagged_only=False)
    return found[0] if found else None


def pick_plan(
    db: Session,
    user: CurrentUser,
    slots: Sequence[SlotRequest],
    exclude_ids: Iterable[int] = (),
) -> list[tuple[SlotRequest, models.Recipe | None]]:
    """Fill every slot, without repeating a recipe inside one plan.

    A slot with no candidate left comes back as None rather than raising: a box
    smaller than the plan is a normal situation ("I have 6 recipes and asked for
    14 meals"), and the caller renders those as empty for the person to fill or
    re-roll later.
    """
    used: list[int] = list(exclude_ids)
    filled: list[tuple[SlotRequest, models.Recipe | None]] = []

    for slot in slots:
        recipe = pick_one(db, user, slot.meal_type, used)
        if recipe is not None:
            used.append(recipe.id)
        filled.append((slot, recipe))

    return filled
