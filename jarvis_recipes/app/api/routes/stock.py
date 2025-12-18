from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from jarvis_recipes.app.api.deps import get_current_user, get_db_session
from jarvis_recipes.app.core.config import get_settings
from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.schemas.stock import StockIngredientRead, StockUnitRead
from jarvis_recipes.app.services import static_seed_service, stock_service, static_recipe_service

router = APIRouter(tags=["stock"])


@router.get("/ingredients/stock", response_model=list[StockIngredientRead])
def get_stock_ingredients(
    q: Optional[str] = Query(default=None),
    limit: int = Query(default=10, ge=1, le=1000),
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    return stock_service.list_stock_ingredients(db, q, limit)


@router.get("/units/stock", response_model=list[StockUnitRead])
def get_stock_units(
    q: Optional[str] = Query(default=None),
    limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    return stock_service.list_stock_units(db, q, limit)


@router.post("/admin/static-data/seed")
def seed_static_data(
    db: Session = Depends(get_db_session),
    admin_secret: Optional[str] = Header(default=None, convert_underscores=False, alias="X-Admin-Secret"),
):
    settings = get_settings()
    if not admin_secret or admin_secret != settings.admin_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin secret")

    base_path = Path(__file__).resolve().parents[4] / "static_data"
    stats = static_seed_service.seed_static_data(db, base_path)
    return stats


@router.post("/admin/static-recipes/seed")
def seed_static_recipes(
    user_id: str,
    db: Session = Depends(get_db_session),
    admin_secret: Optional[str] = Header(default=None, convert_underscores=False, alias="X-Admin-Secret"),
):
    settings = get_settings()
    if not admin_secret or admin_secret != settings.admin_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin secret")
    base_path = Path(__file__).resolve().parents[4] / "static_data"
    stats = static_recipe_service.seed_stock_recipes(db, base_path, user_id)
    return stats

