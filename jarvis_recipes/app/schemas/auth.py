from typing import Optional

from pydantic import BaseModel


class CurrentUser(BaseModel):
    """The authenticated caller.

    `household_id` has always been in the JWT that jarvis-auth mints; this
    service simply discarded it. Recipes and meal plans are scoped to the
    household, not the person, so a family plans from one shared box -- see
    api/deps.py and services/scoping.py.

    It is Optional because a token minted before household membership existed,
    or for a user who belongs to no household, has no claim. Those callers fall
    back to seeing only their own rows.
    """

    id: int
    email: Optional[str] = None
    household_id: Optional[str] = None
