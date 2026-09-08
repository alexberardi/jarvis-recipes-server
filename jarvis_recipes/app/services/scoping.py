"""One place that decides who can see a row.

Recipes and meal plans belong to a HOUSEHOLD, not a person: a family plans from
one shared box. Before this existed, 49 call sites each wrote their own
`user_id == current_user.id` filter, which is exactly the shape of thing that
drifts -- one missed filter is a data leak, one over-applied filter is a recipe
that vanishes.

The rule, in one predicate:

    visible if  household_id == caller's household
            or  (household_id IS NULL and user_id == caller)

The NULL arm is the migration ramp. This service cannot resolve a user's
household on its own -- that lives in jarvis-auth -- so pre-existing rows keep
`household_id = NULL` until scripts/backfill_household_ids.py fills them in.
Without that arm, deploying the migration would make every existing recipe
disappear until the backfill finished. With it, nothing is ever invisible to the
person who created it.

A caller with no household at all (no claim in the token, or belongs to none)
sees exactly their own rows, which is the old behaviour.
"""
from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList

from jarvis_recipes.app.schemas.auth import CurrentUser


def visible_to(model, user: CurrentUser) -> BinaryExpression | BooleanClauseList:
    """SQLAlchemy filter for rows `user` may read.

    Args:
        model: a mapped class with `user_id` and `household_id` columns.
        user: the authenticated caller.
    """
    owned = model.user_id == str(user.id)
    if not user.household_id:
        return owned
    return or_(
        model.household_id == user.household_id,
        # Not yet backfilled: still the author's, still visible to them.
        (model.household_id.is_(None)) & owned,
    )


def owned_by(model, user: CurrentUser) -> BinaryExpression:
    """Filter for rows the caller may MUTATE.

    Deliberately narrower than `visible_to` is not the choice here -- household
    members share a box, so they may edit each other's recipes. This exists so
    that intent is explicit at each call site rather than implied, and so a
    future "only the author may delete" rule has one place to live.
    """
    return visible_to(model, user)


def household_for_write(user: CurrentUser) -> str | None:
    """The household_id to stamp on a row this caller is creating."""
    return user.household_id
