"""Staples: ingredients the household always has in.

Name-keyed rather than id-keyed on the way in, because the client's handle on an
ingredient is the shopping-list key it already received -- it has no id to send
until it has read this list. Removal takes an id, which the client has by then.
"""
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from jarvis_recipes.app.api.deps import get_current_user, get_db_session
from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.services import staples_service

router = APIRouter(prefix="/staples", tags=["staples"])


class StapleRead(BaseModel):
    id: int
    # The normalized shopping-list key, which is what the list's item names are,
    # so a client can match the two without normalizing anything itself.
    name: str


class StapleCreate(BaseModel):
    # Raw text is fine -- a shopping-list key, or something typed by a person.
    # The server normalizes, so the client never has to reproduce that logic.
    name: str = Field(..., min_length=1, max_length=200)


@router.get("", response_model=list[StapleRead])
def list_staples(
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    return staples_service.list_staples(db, current_user)


@router.post("", response_model=StapleRead, status_code=status.HTTP_201_CREATED)
def add_staple(
    payload: StapleCreate,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Idempotent: adding an existing staple returns it rather than erroring.

    201 either way. The client is a toggle and does not distinguish, and a 409
    would only give it a second path to write for the same outcome.
    """
    return staples_service.add_staple(db, current_user, payload.name)


@router.delete("/{staple_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_staple(
    staple_id: int,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    staples_service.remove_staple(db, current_user, staple_id)
