import json
from pathlib import Path
from typing import Dict

from sqlalchemy import select
from sqlalchemy.orm import Session

from jarvis_recipes.app.db import models


def _load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def seed_static_data(db: Session, base_path: Path) -> Dict[str, int]:
    ingredients_path = base_path / "ingredients.json"
    units_path = base_path / "units_of_measure.json"
    stats = {
        "ingredients_inserted": 0,
        "ingredients_updated": 0,
        "units_inserted": 0,
        "units_updated": 0,
    }

    if ingredients_path.exists():
        data = _load_json(ingredients_path)
        for item in data:
            name = item["name"].strip()
            category = item.get("category")
            synonyms_list = item.get("synonyms") or []
            synonyms = ", ".join(s.strip() for s in synonyms_list if s and str(s).strip())
            stmt = select(models.StockIngredient).where(models.StockIngredient.name.ilike(name))
            existing = db.scalars(stmt).first()
            if existing:
                existing.name = name
                existing.category = category
                existing.synonyms = synonyms
                stats["ingredients_updated"] += 1
            else:
                db.add(models.StockIngredient(name=name, category=category, synonyms=synonyms))
                stats["ingredients_inserted"] += 1

    if units_path.exists():
        data = _load_json(units_path)
        for item in data:
            name = item["name"].strip()
            abbr = item.get("abbreviation")
            stmt = select(models.StockUnitOfMeasure).where(models.StockUnitOfMeasure.name.ilike(name))
            existing = db.scalars(stmt).first()
            if existing:
                existing.name = name
                existing.abbreviation = abbr
                stats["units_updated"] += 1
            else:
                db.add(models.StockUnitOfMeasure(name=name, abbreviation=abbr))
                stats["units_inserted"] += 1

    db.commit()
    return stats

