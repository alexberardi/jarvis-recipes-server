import json
import re
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from jarvis_recipes.app.db import models
from jarvis_recipes.app.services import url_recipe_parser


def _split_qty_unit(qty: str) -> Tuple[str | None, str | None]:
    units = {
        "cup",
        "cups",
        "teaspoon",
        "teaspoons",
        "tsp",
        "tbsp",
        "tablespoon",
        "tablespoons",
        "ounce",
        "ounces",
        "oz",
        "pound",
        "pounds",
        "lb",
        "lbs",
        "gram",
        "grams",
        "g",
        "kg",
        "milliliter",
        "milliliters",
        "ml",
        "liter",
        "liters",
        "l",
        "pinch",
        "pinches",
        "clove",
        "cloves",
        "can",
        "cans",
        "package",
        "packages",
        "stick",
        "sticks",
        "slice",
        "slices",
        "piece",
        "pieces",
    }
    if not qty:
        return None, None
    raw_tokens = qty.replace("(", " ").replace(")", " ").replace(",", " ").split()
    tokens = [t for t in raw_tokens if t]
    if len(tokens) < 2:
        return qty, None
    # Prefer a unit in the first 3 tokens (e.g., "1 pound (85% lean)" or "3/4 teaspoon")
    def _should_split(qty_part: str) -> bool:
        if not qty_part:
            return True  # allow cases like "pinch salt"
        return bool(re.match(r"^[0-9/]", qty_part))

    for idx, tok in enumerate(tokens[:3]):
        tok_clean = tok.lower().strip(".,")
        if tok_clean in units:
            qty_part = " ".join(tokens[:idx]).strip()
            if not _should_split(qty_part):
                continue
            return qty_part or None, tok.rstrip(".,")
    # Fallback: check trailing token
    last_raw = tokens[-1]
    last_clean = last_raw.lower().strip(".,")
    if last_clean in units:
        qty_part = " ".join(tokens[:-1]).strip()
        if _should_split(qty_part):
            return qty_part or None, last_raw.rstrip(".,")
    return qty, None


class RecipeParseJobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    ERROR = "ERROR"
    CANCELED = "CANCELED"
    COMMITTED = "COMMITTED"
    ABANDONED = "ABANDONED"


def create_job(
    db: Session,
    user_id: str,
    url: Optional[str] = None,
    use_llm_fallback: bool = True,
    job_type: str = "url",
    job_data: Optional[dict] = None,
) -> models.RecipeParseJob:
    job = models.RecipeParseJob(
        id=str(uuid.uuid4()),
        user_id=str(user_id),
        job_type=job_type,
        url=url,
        use_llm_fallback=use_llm_fallback,
        job_data=job_data,
        status=RecipeParseJobStatus.PENDING.value,
        attempts=0,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def create_image_job(db: Session, user_id: str, ingestion_id: str, job_data: Optional[dict] = None) -> models.RecipeParseJob:
    payload = {"ingestion_id": ingestion_id}
    if job_data:
        payload.update(job_data)
    return create_job(db=db, user_id=user_id, job_type="image", job_data=payload, use_llm_fallback=False)


def create_ingestion_job(db: Session, user_id: str, input_payload: dict) -> models.RecipeParseJob:
    return create_job(db=db, user_id=user_id, job_type="ingestion", job_data=input_payload, use_llm_fallback=False)


def get_job_for_user(db: Session, job_id: str, user_id: str) -> Optional[models.RecipeParseJob]:
    stmt = select(models.RecipeParseJob).where(models.RecipeParseJob.id == job_id, models.RecipeParseJob.user_id == str(user_id))
    return db.scalars(stmt).first()


def fetch_next_pending(db: Session, job_type: str = "url") -> Optional[models.RecipeParseJob]:
    stmt = (
        select(models.RecipeParseJob)
        .where(
            models.RecipeParseJob.status == RecipeParseJobStatus.PENDING.value,
            models.RecipeParseJob.job_type == job_type,
        )
        .order_by(models.RecipeParseJob.created_at.asc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def mark_running(db: Session, job: models.RecipeParseJob) -> None:
    if job.status in {s.value for s in (RecipeParseJobStatus.CANCELED, RecipeParseJobStatus.COMMITTED, RecipeParseJobStatus.ABANDONED)}:
        return
    job.status = RecipeParseJobStatus.RUNNING.value
    job.started_at = datetime.utcnow()
    job.attempts = (job.attempts or 0) + 1
    db.commit()
    db.refresh(job)


def mark_complete(db: Session, job: models.RecipeParseJob, result: url_recipe_parser.ParseResult) -> None:
    if job.status in {s.value for s in (RecipeParseJobStatus.CANCELED, RecipeParseJobStatus.COMMITTED, RecipeParseJobStatus.ABANDONED)}:
        return
    job.status = RecipeParseJobStatus.COMPLETE.value
    job.completed_at = datetime.utcnow()
    payload = json.loads(result.model_dump_json())

    def _recipe_dict_to_draft(recipe: dict, source_type: str) -> dict:
        ingredients = []
        for ing in recipe.get("ingredients") or []:
            qty_val = ing.get("quantity_display") or ing.get("quantity")
            qty_val, unit_val = _split_qty_unit(qty_val) if qty_val else (None, None)
            ingredients.append(
                {
                    "name": ing.get("text") or ing.get("name") or ing.get("label") or "",
                    "quantity": qty_val or ing.get("quantity_display") or ing.get("quantity"),
                    "unit": ing.get("unit") or unit_val,
                    "notes": None,
                }
            )
        est_time = (
            recipe.get("estimated_time_minutes")
            or recipe.get("total_time_minutes")
            or recipe.get("cook_time_minutes")
            or recipe.get("totalTime")
            or 0
        )
        prep_time = recipe.get("prep_time_minutes") or recipe.get("prepTime") or 0
        cook_time = recipe.get("cook_time_minutes") or recipe.get("cookTime") or est_time
        total_time = recipe.get("total_time_minutes") or (prep_time + cook_time if (prep_time or cook_time) else est_time)
        source_obj = recipe.get("source") or {}
        return {
            "title": recipe.get("title") or "Untitled",
            "description": recipe.get("description"),
            "ingredients": ingredients,
            "steps": recipe.get("steps") or [],
            "prep_time_minutes": prep_time,
            "cook_time_minutes": cook_time,
            "total_time_minutes": total_time,
            "servings": recipe.get("servings"),
            "tags": recipe.get("tags") or [],
            "source": {
                "type": source_obj.get("type") or source_type,
                "source_url": source_obj.get("source_url") or recipe.get("source_url"),
                "image_url": source_obj.get("image_url") or recipe.get("image_url"),
            },
        }

    if job.job_type in {"url", "ingestion", "image"}:
        recipe = payload.get("recipe") or payload.get("recipe_draft") or {}
        source_type = "url" if job.job_type in {"url", "ingestion"} else "ocr"
        recipe_draft = _recipe_dict_to_draft(recipe, source_type)
        pipe = payload.get("pipeline") or {}
        job.result_json = {
            "recipe_draft": recipe_draft,
            "pipeline": {
                "parser_strategy": pipe.get("parser_strategy") or payload.get("parser_strategy"),
                "used_llm": pipe.get("used_llm") or payload.get("used_llm"),
                "warnings": pipe.get("warnings") or payload.get("warnings") or [],
                "source_url": recipe.get("source_url") if recipe else None,
                "error_code": pipe.get("error_code") or payload.get("error_code"),
                "error_message": pipe.get("error_message") or payload.get("error_message"),
                "raw_pipeline": pipe if pipe else None,
            },
        }
    else:
        job.result_json = payload
    db.commit()
    db.refresh(job)


def mark_error(db: Session, job: models.RecipeParseJob, error_code: str, error_message: str) -> None:
    if job.status in {s.value for s in (RecipeParseJobStatus.CANCELED, RecipeParseJobStatus.COMMITTED, RecipeParseJobStatus.ABANDONED)}:
        return
    job.status = RecipeParseJobStatus.ERROR.value
    job.completed_at = datetime.utcnow()
    job.error_code = error_code
    job.error_message = error_message
    db.commit()
    db.refresh(job)


def mark_committed(db: Session, job: models.RecipeParseJob) -> None:
    if job.status != RecipeParseJobStatus.COMPLETE.value:
        return
    job.status = RecipeParseJobStatus.COMMITTED.value
    job.committed_at = datetime.utcnow()
    db.commit()
    db.refresh(job)


def mark_canceled(db: Session, job: models.RecipeParseJob) -> bool:
    if job.status not in {RecipeParseJobStatus.PENDING.value, RecipeParseJobStatus.RUNNING.value}:
        return False
    job.status = RecipeParseJobStatus.CANCELED.value
    job.canceled_at = datetime.utcnow()
    db.commit()
    db.refresh(job)
    return True


def abandon_stale_jobs(db: Session, cutoff_minutes: int) -> int:
    cutoff = datetime.utcnow() - timedelta(minutes=cutoff_minutes)
    stmt = (
        update(models.RecipeParseJob)
        .where(
            models.RecipeParseJob.status == RecipeParseJobStatus.COMPLETE.value,
            models.RecipeParseJob.completed_at != None,  # noqa: E711
            models.RecipeParseJob.completed_at < cutoff,
        )
        .values(
            status=RecipeParseJobStatus.ABANDONED.value,
            abandoned_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
    )
    result = db.execute(stmt)
    db.commit()
    return result.rowcount or 0

