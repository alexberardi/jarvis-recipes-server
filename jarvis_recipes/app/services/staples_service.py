"""Ingredients the household always has in.

Salt, oil, pepper and butter turn up in most recipes and therefore on every
shopping list, where they crowd out the six things actually worth a trip. A
staple stays visible -- see shopping_list_service for why nothing is ever
dropped from the list -- but it is flagged so the client can group it away, and
it is left out of the cart.

Names are stored as shopping-list KEYS (normalize_name output) so that
"2 tbsp Olive Oil" in a recipe and "olive oil" typed by a person meet on the
same row. This module imports that normalizer; shopping_list_service must not
import this one back, or the two become circular. The direction is deliberate:
the list is the lower layer and knows nothing about staples, which is why
`build()` takes the names as an argument instead of fetching them.
"""
from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from jarvis_recipes.app.db import models
from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.services.scoping import household_for_write, owned_by, visible_to
from jarvis_recipes.app.services.shopping_list_service import normalize_name


def list_staples(db: Session, user: CurrentUser) -> list[models.Staple]:
    """Every staple the caller can see, one row per name.

    Deduped because the unique constraint is per author: two household members
    can each have added "salt", and the client should see one entry, not two.
    The oldest row wins so the id a client holds stays stable.
    """
    rows = db.scalars(
        select(models.Staple)
        .where(visible_to(models.Staple, user))
        .order_by(models.Staple.name, models.Staple.id)
    ).all()
    seen: dict[str, models.Staple] = {}
    for row in rows:
        seen.setdefault(row.name, row)
    return list(seen.values())


def staple_names(db: Session, user: CurrentUser) -> set[str]:
    """Just the keys, for flagging a shopping list or filtering a cart."""
    return {s.name for s in list_staples(db, user)}


def add_staple(db: Session, user: CurrentUser, text: str) -> models.Staple:
    """Mark an ingredient as always-in-stock. Idempotent.

    Takes the raw text a client has -- a shopping-list key, or something a person
    typed -- and normalizes it. Adding the same thing twice returns the existing
    row rather than erroring: the client is a toggle, and a 409 would make it
    fiddly for no benefit.
    """
    name = normalize_name(text)
    if not name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A staple needs a name",
        )

    existing = db.scalars(
        select(models.Staple)
        .where(visible_to(models.Staple, user))
        .where(models.Staple.name == name)
        .order_by(models.Staple.id)
    ).first()
    if existing is not None:
        return existing

    staple = models.Staple(
        user_id=str(user.id),
        household_id=household_for_write(user),
        name=name,
    )
    db.add(staple)
    db.commit()
    db.refresh(staple)
    return staple


def remove_staple(db: Session, user: CurrentUser, staple_id: int) -> None:
    """Stop treating an ingredient as a staple.

    Removes every row with that name the caller can mutate, not just the one id:
    a household where two members each added "salt" would otherwise need two
    taps to stop seeing it, and the second tap would look like a bug.

    404 rather than 403 for someone else's row -- a distinct status would confirm
    the id exists, which is a membership oracle.
    """
    staple = db.scalars(
        select(models.Staple)
        .where(owned_by(models.Staple, user))
        .where(models.Staple.id == staple_id)
    ).first()
    if staple is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Staple not found"
        )

    same_name = db.scalars(
        select(models.Staple)
        .where(owned_by(models.Staple, user))
        .where(models.Staple.name == staple.name)
    ).all()
    for row in same_name:
        db.delete(row)
    db.commit()
