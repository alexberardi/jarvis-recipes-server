import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from jarvis_recipes.app.db import models


@lru_cache(maxsize=1)
def _load_stock_recipes(base_path: Path) -> List[Dict[str, Any]]:
    path = base_path / "stock_recipes.json"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def list_stock_recipes(base_path: Path, q: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    data = _load_stock_recipes(base_path)
    if q:
        q_lower = q.lower()
        data = [r for r in data if q_lower in r.get("title", "").lower() or any(q_lower in t.lower() for t in r.get("tags", []))]
    return data[:limit]


def seed_stock_recipes(db: Session, base_path: Path, user_id: str) -> Dict[str, int]:
    """Insert stock recipes into the recipes table for a given user (for testing/dev)."""
    data = _load_stock_recipes(base_path)
    stats = {"inserted": 0, "skipped": 0}
    if not data:
        return stats

    existing_titles = {
        r.title.lower(): r.id
        for r in db.query(models.Recipe)
        .filter(models.Recipe.user_id == user_id)
        .all()
    }

    for item in data:
        title = item.get("title") or "Untitled"
        title_key = title.lower()
        if title_key in existing_titles:
            stats["skipped"] += 1
            continue

        recipe = models.Recipe(
            user_id=user_id,
            title=title,
            description=item.get("description"),
            source_type=models.SourceType.MANUAL,
            servings=None,
            total_time_minutes=(item.get("prep_time_minutes") or 0) + (item.get("cook_time_minutes") or 0),
        )
        db.add(recipe)
        db.flush()

        # tags
        tag_names = item.get("tags") or []
        for tn in tag_names:
            tn_clean = tn.strip()
            if not tn_clean:
                continue
            tag = db.query(models.Tag).filter(models.Tag.name.ilike(tn_clean)).first()
            if not tag:
                tag = models.Tag(name=tn_clean)
                db.add(tag)
                db.flush()
            recipe.tags.append(tag)

        # ingredients
        for ing_text in item.get("ingredients") or []:
            if not ing_text:
                continue
            db.add(models.Ingredient(recipe_id=recipe.id, text=str(ing_text)))

        # steps
        for idx, step_text in enumerate(item.get("steps") or []):
            if not step_text:
                continue
            db.add(models.Step(recipe_id=recipe.id, step_number=idx + 1, text=str(step_text)))

        stats["inserted"] += 1

    db.commit()
    return stats

