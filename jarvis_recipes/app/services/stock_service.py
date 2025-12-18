from typing import List, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from jarvis_recipes.app.db import models


def list_stock_ingredients(db: Session, q: Optional[str], limit: int = 10) -> List[models.StockIngredient]:
    stmt = select(models.StockIngredient).order_by(models.StockIngredient.name.asc()).limit(limit)
    if q:
        like = f"%{q.lower()}%"
        stmt = (
            select(models.StockIngredient)
            .where(models.StockIngredient.name.ilike(like))
            .order_by(models.StockIngredient.name.asc())
            .limit(limit)
        )
    return list(db.scalars(stmt).all())


def list_stock_units(db: Session, q: Optional[str], limit: int = 10) -> List[models.StockUnitOfMeasure]:
    stmt = select(models.StockUnitOfMeasure).order_by(models.StockUnitOfMeasure.name.asc()).limit(limit)
    if q:
        like = f"%{q.lower()}%"
        stmt = (
            select(models.StockUnitOfMeasure)
            .where(
                or_(
                    models.StockUnitOfMeasure.name.ilike(like),
                    models.StockUnitOfMeasure.abbreviation.ilike(like),
                )
            )
            .order_by(models.StockUnitOfMeasure.name.asc())
            .limit(limit)
        )
    return list(db.scalars(stmt).all())

