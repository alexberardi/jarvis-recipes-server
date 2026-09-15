"""Shopping list routes.

The list is a recomputed VIEW over committed plans for a date range, never a
stored artifact -- see services/shopping_list_service for why.
"""
from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from jarvis_recipes.app.api.deps import get_current_user, get_db_session
from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.services import shopping_list_service, staples_service

router = APIRouter(prefix="/shopping-list", tags=["shopping"])


class AmountRead(BaseModel):
    unit: str | None = None
    quantity: float | None = None
    # Lines whose quantity could not be parsed, kept verbatim rather than dropped
    # -- "salt and pepper to taste" still belongs on the list.
    unparsed: list[str] = []


class ShoppingItemRead(BaseModel):
    name: str
    amounts: list[AmountRead]
    recipes: list[str]
    # Always in the cupboard. Still returned -- the client groups it away rather
    # than the server hiding it, so an ingredient is never silently absent from
    # a list someone is cooking from.
    is_staple: bool = False


class ShoppingListRead(BaseModel):
    start_date: date
    end_date: date
    items: list[ShoppingItemRead]
    # Zero means there is nothing planned in that range, which the client should
    # say plainly rather than showing an empty list that looks like a failure.
    plan_count: int


@router.get("", response_model=ShoppingListRead)
def get_shopping_list(
    start_date: date = Query(..., description="First day to shop for, inclusive"),
    end_date: date = Query(..., description="Last day to shop for, inclusive"),
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    plans = shopping_list_service.plans_in_range(db, current_user, start_date, end_date)
    items = shopping_list_service.build(
        db,
        current_user,
        start_date,
        end_date,
        staple_names=staples_service.staple_names(db, current_user),
    )
    return ShoppingListRead(
        start_date=start_date,
        end_date=end_date,
        plan_count=len(plans),
        items=[
            ShoppingItemRead(
                name=i.name,
                amounts=[
                    AmountRead(
                        unit=a.unit,
                        quantity=float(a.quantity) if a.quantity is not None else None,
                        unparsed=a.unparsed,
                    )
                    for a in i.amounts
                ],
                recipes=i.recipes,
                is_staple=i.is_staple,
            )
            for i in items
        ],
    )
