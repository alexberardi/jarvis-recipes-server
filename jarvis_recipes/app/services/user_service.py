"""Local user rows mirroring jarvis-auth identities.

This service keeps its own ``users`` table so recipes, meal plans, ingestions,
staged recipes and mailbox messages can carry a foreign key. Nothing populates
it up front -- jarvis-auth owns identity, and recipes only learns a user exists
the first time they write something. Every write path storing a ``user_id`` has
to make sure the row is there first.

Skipping it is a first-use bug that only appears on a fresh database. On prod,
photo import inserted a RecipeIngestion without ensuring the user and returned
500:

    ForeignKeyViolation: insert or update on table "recipe_ingestions" violates
    foreign key constraint "recipe_ingestions_user_id_fkey"
    DETAIL: Key (user_id)=(1) is not present in table "users".

Saving a recipe worked, because that path did ensure it -- so which features
worked depended on the order they happened to be used in, and photo import
required having already saved a recipe some other way.

One implementation, imported everywhere, so a new write path does not get to
quietly reinvent it.
"""

from sqlalchemy.orm import Session

from jarvis_recipes.app.db import models


def ensure_user(db: Session, user_id: int | str) -> models.User:
    """Return the local row for *user_id*, creating it if this is the first write.

    Flushes rather than commits: the caller owns the transaction, so the new row
    lands with whatever it is about to insert alongside it.
    """
    user_id_str = str(user_id)
    user = db.get(models.User, user_id_str)
    if user is None:
        user = models.User(user_id=user_id_str)
        db.add(user)
        db.flush()
    return user
