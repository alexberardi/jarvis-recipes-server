"""Grocery cart export and the ingredient -> SKU map behind it.

See services/grocery_service for why the map lives in this service and why the
LLM never invents a SKU.
"""
from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from jarvis_recipes.app.api.deps import get_current_user, get_db_session
from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.services import grocery_service

router = APIRouter(prefix="/grocery", tags=["grocery"])

Retailer = Literal["walmart"]


class SkuMappingRead(BaseModel):
    id: int
    retailer: str
    ingredient_name: str
    sku: str
    product_name: Optional[str] = None
    unit_size: Optional[str] = None
    source: str

    model_config = {"from_attributes": True}


class SkuMappingWrite(BaseModel):
    # A shopping-list key, as returned in `unmatched[].ingredient_name` -- not a
    # raw recipe line. Set `raw` when sending one of those instead.
    ingredient_name: str = Field(..., min_length=1, max_length=255)
    raw: bool = False
    sku: str = Field(..., min_length=1, max_length=64)
    retailer: Retailer = "walmart"
    product_name: Optional[str] = Field(None, max_length=512)
    unit_size: Optional[str] = Field(None, max_length=64)


class CartItemRead(BaseModel):
    ingredient_name: str
    sku: str
    quantity: int
    product_name: Optional[str] = None
    unit_size: Optional[str] = None
    source: str


class UnmatchedItemRead(BaseModel):
    ingredient_name: str
    amount_display: str
    recipes: list[str]


class CartRead(BaseModel):
    retailer: str
    # None when nothing matched: an empty `items=` opens Walmart with an empty
    # cart and no explanation, which reads as a broken export.
    url: Optional[str] = None
    items: list[CartItemRead]
    unmatched: list[UnmatchedItemRead]
    # Present when a background pass was queued to learn the unmatched names.
    # The client does not need to wait for it -- the cart above is already usable.
    match_job_id: Optional[str] = None


@router.get("/sku-map", response_model=list[SkuMappingRead])
def list_sku_map(
    retailer: Retailer = "walmart",
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    return grocery_service.list_map(db, current_user, retailer)


@router.put("/sku-map", response_model=SkuMappingRead)
def upsert_sku_mapping(
    payload: SkuMappingWrite,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Record what this household buys for an ingredient.

    Always `source="manual"`: this endpoint is only ever reached by a person
    choosing a product. The background pass writes through the service directly.
    """
    name = (
        grocery_service.map_key(payload.ingredient_name)
        if payload.raw
        else payload.ingredient_name
    )
    return grocery_service.upsert_mapping(
        db,
        current_user,
        ingredient_name=name,
        sku=payload.sku,
        retailer=payload.retailer,
        product_name=payload.product_name,
        unit_size=payload.unit_size,
        source="manual",
    )


@router.delete("/sku-map/{mapping_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sku_mapping(
    mapping_id: int,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    if not grocery_service.delete_mapping(db, current_user, mapping_id):
        # 404 rather than 403 for another household's row: a distinct status
        # would confirm the id exists.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mapping not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/cart", response_model=CartRead)
def build_cart(
    start_date: date = Query(..., description="First day to shop for, inclusive"),
    end_date: date = Query(..., description="Last day to shop for, inclusive"),
    retailer: Retailer = "walmart",
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Build a cart link for a date range.

    Returns immediately with whatever the map already knows, and queues the rest
    for background matching. A shopper standing in the kitchen should not wait on
    a model to get a link.
    """
    if end_date < start_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end_date must not be before start_date",
        )

    cart = grocery_service.build_cart(db, current_user, start_date, end_date, retailer)
    cart.match_job_id = grocery_service.enqueue_match_pass(
        db, current_user, [u.ingredient_name for u in cart.unmatched], retailer
    )
    return cart
