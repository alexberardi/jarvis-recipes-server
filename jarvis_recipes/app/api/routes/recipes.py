import logging
import uuid
from typing import List, Optional
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pathlib import Path
from pydantic import AnyHttpUrl, BaseModel, Field
from sqlalchemy.orm import Session

from urllib.parse import urlparse

from jarvis_recipes.app.api.deps import get_current_user, get_db_session
from jarvis_recipes.app.core.config import get_settings
from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.schemas.recipe import RecipeCreate, RecipeRead, RecipeUpdate
from jarvis_recipes.app.services import recipes_service
from jarvis_recipes.app.services import url_recipe_parser
from jarvis_recipes.app.db import models
from jarvis_recipes.app.schemas.parse_job import ParseJobCreate, ParseJobStatus
from jarvis_recipes.app.services import parse_job_service
from jarvis_recipes.app.services.url_recipe_parser import preflight_validate_url
from jarvis_recipes.app.db import models as db_models
from jarvis_recipes.app.services import static_recipe_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/recipes", tags=["recipes"])


class ParseUrlRequest(BaseModel):
    url: AnyHttpUrl
    use_llm_fallback: bool = True
    save: bool = False


class ParseUrlResponse(BaseModel):
    success: bool
    recipe: Optional[url_recipe_parser.ParsedRecipe] = None
    created_recipe_id: Optional[int] = None
    warnings: List[str] = Field(default_factory=list)
    used_llm: bool = False
    parser_strategy: Optional[str] = None
    message: Optional[str] = None
    details: Optional[str] = None
    error_code: Optional[str] = None


@router.post("", response_model=RecipeRead, status_code=status.HTTP_201_CREATED)
def create_recipe(
    payload: RecipeCreate,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    parse_job_id = payload.parse_job_id
    if parse_job_id:
        job = parse_job_service.get_job_for_user(db, parse_job_id, current_user.id)
        if not job:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parse job not found")
        if job.status != parse_job_service.RecipeParseJobStatus.COMPLETE.value:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Parse job not ready")

    recipe = recipes_service.create_recipe(db, current_user.id, payload)

    if parse_job_id:
        # Mark job as committed
        parse_job_service.mark_committed(db, job)

    return recipe


@router.get("/user/{recipe_id}", response_model=RecipeRead)
def get_user_recipe(
    recipe_id: int,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    recipe = recipes_service.get_recipe(db, current_user.id, recipe_id)
    if not recipe or recipe.user_id != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe not found")
    return recipe


@router.get("/stage/{stage_id}")
def get_stage_recipe(
    stage_id: str,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    stage = db.get(models.StageRecipe, stage_id)
    if not stage or stage.user_id != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if stage.expires_at <= datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Stage recipe expired")
    return {
        "id": stage.id,
        "title": stage.title,
        "description": stage.description,
        "yield": stage.yield_text,
        "prep_time_minutes": stage.prep_time_minutes,
        "cook_time_minutes": stage.cook_time_minutes,
        "ingredients": stage.ingredients,
        "steps": stage.steps,
        "tags": stage.tags,
        "notes": stage.notes,
    }


@router.get("/core/{recipe_id}")
def get_core_recipe(recipe_id: str):
    # Placeholder: no core store implemented; return 404
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Core recipe not found")


@router.get("/stock")
def get_stock_recipes(
    q: Optional[str] = None,
    limit: int = 50,
    current_user: CurrentUser = Depends(get_current_user),
):
    base_path = Path(__file__).resolve().parents[4] / "static_data"
    return static_recipe_service.list_stock_recipes(base_path, q, limit)


@router.get("", response_model=list[RecipeRead])
def list_recipes(
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    return recipes_service.list_recipes(db, current_user.id)


@router.get("/{recipe_id}", response_model=RecipeRead)
def get_recipe(
    recipe_id: int,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    return recipes_service.get_recipe(db, current_user.id, recipe_id)


@router.patch("/{recipe_id}", response_model=RecipeRead)
def update_recipe(
    recipe_id: int,
    payload: RecipeUpdate,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    return recipes_service.update_recipe(db, current_user.id, recipe_id, payload)


@router.delete("/{recipe_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_recipe(
    recipe_id: int,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    recipes_service.delete_recipe(db, current_user.id, recipe_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/parse-url", response_model=ParseUrlResponse)
async def parse_recipe_from_url_endpoint(
    payload: ParseUrlRequest,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    result = await url_recipe_parser.parse_recipe_from_url(str(payload.url), payload.use_llm_fallback)

    if not result.success or not result.recipe:
        message = result.error_message or "Unable to parse recipe from URL"
        return ParseUrlResponse(
            success=False,
            recipe=None,
            created_recipe_id=None,
            warnings=result.warnings,
            used_llm=result.used_llm,
            parser_strategy=result.parser_strategy,
            error_code=result.error_code or "parse_failed",
            message=message,
            details=message,
        )

    if not payload.save:
        return ParseUrlResponse(
            success=True,
            recipe=result.recipe,
            created_recipe_id=None,
            warnings=result.warnings,
            used_llm=result.used_llm,
            parser_strategy=result.parser_strategy,
        )

    try:
        normalized = url_recipe_parser.normalize_parsed_recipe(result.recipe)
        created = recipes_service.create_recipe(db, current_user.id, normalized)
        return ParseUrlResponse(
            success=True,
            recipe=result.recipe,
            created_recipe_id=created.id,
            warnings=result.warnings,
            used_llm=result.used_llm,
            parser_strategy=result.parser_strategy,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return ParseUrlResponse(
            success=False,
            recipe=None,
            created_recipe_id=None,
            warnings=result.warnings,
            used_llm=result.used_llm,
            parser_strategy=result.parser_strategy,
            error_code="save_failed",
            message="Failed to save recipe",
            details=detail,
        )


@router.post("/parse-url/async", response_model=ParseJobStatus)
async def enqueue_parse_recipe(
    payload: ParseJobCreate,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Enqueue a recipe parsing job from URL.
    
    Note: This endpoint now always requires client-side webview extraction.
    The client should extract JSON-LD and/or HTML from a webview and submit
    to /recipes/parse-payload/async instead.
    
    This endpoint performs basic URL validation and returns next_action="webview_extract"
    to guide the client to use the webview pattern.
    """
    job_id = str(uuid.uuid4())
    preflight = await preflight_validate_url(str(payload.url))
    if not preflight.ok:
        detail = {
            "error_code": preflight.error_code or "preflight_failed",
            "message": preflight.error_message,
            "status_code": preflight.status_code,
            "job_id": job_id,
        }
        # Include next_action if preflight detected encoding/blocking issues
        if preflight.next_action:
            detail["next_action"] = preflight.next_action
            detail["next_action_reason"] = preflight.next_action_reason
        raise HTTPException(
            status_code=400,
            detail=detail,
        )
    
    # Always require webview extraction - skip server-side fetch
    # This avoids encoding issues and works better with modern websites
    return ParseJobStatus(
        id=job_id,
        status="PENDING",
        next_action="webview_extract",
        next_action_reason="webview_required",
    )


@router.get("/parse-url/status/{job_id}", response_model=ParseJobStatus)
@router.get("/jobs/{job_id}", response_model=ParseJobStatus)
def get_parse_job_status(
    job_id: str,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    logger.debug("get_parse_job_status: user_id=%s, job_id=%s", current_user.id, job_id)
    job = parse_job_service.get_job_for_user(db, job_id, current_user.id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    result = job.result_json if job.result_json else None
    return ParseJobStatus(
        id=job.id,
        status=job.status,
        result=result,
        error_code=job.error_code,
        error_message=job.error_message,
    )


@router.get("/parse-url/jobs")
@router.get("/jobs")
def list_parse_jobs(
    status: str | None = "COMPLETE",
    include_expired: bool = False,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    settings = get_settings()
    cutoff_dt = datetime.utcnow() - timedelta(minutes=settings.recipe_parse_job_abandon_minutes)

    query = (
        db.query(db_models.RecipeParseJob)
        .filter(db_models.RecipeParseJob.user_id == str(current_user.id))
        .order_by(db_models.RecipeParseJob.completed_at.desc().nullslast())
    )

    if status:
        query = query.filter(db_models.RecipeParseJob.status == status)

    if not include_expired and (status is None or status == parse_job_service.RecipeParseJobStatus.COMPLETE.value):
        query = query.filter(
            db_models.RecipeParseJob.completed_at != None,  # noqa: E711
            db_models.RecipeParseJob.completed_at >= cutoff_dt,
        )

    jobs = query.limit(50).all()

    def preview_from_result(res: dict | None) -> dict | None:
        if not res or not isinstance(res, dict):
            return None
        recipe = res.get("recipe") if isinstance(res.get("recipe"), dict) else res.get("recipe")
        title = recipe.get("title") if isinstance(recipe, dict) else None
        src = None
        if recipe and isinstance(recipe, dict):
            src = recipe.get("source_url")
        if not src:
            src = res.get("source_url")
        source_host = None
        if src:
            try:
                source_host = urlparse(src).hostname
            except ValueError:
                source_host = None
        return {"title": title, "source_host": source_host}

    response_jobs = []
    for job in jobs:
        warnings = []
        if job.result_json and isinstance(job.result_json, dict):
            w = job.result_json.get("warnings")
            if isinstance(w, list):
                warnings = w
        response_jobs.append(
            {
                "id": job.id,
                "job_type": job.job_type,
                "url": job.url,
                "status": job.status,
                "completed_at": job.completed_at,
                "warnings": warnings,
                "preview": preview_from_result(job.result_json),
            }
        )

    return {"jobs": response_jobs}


@router.post("/parse-url/jobs/{job_id}/cancel", response_model=ParseJobStatus)
@router.post("/jobs/{job_id}/cancel", response_model=ParseJobStatus)
def cancel_parse_job(
    job_id: str,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    job = parse_job_service.get_job_for_user(db, job_id, current_user.id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    
    # Allow canceling COMPLETE jobs - just mark as CANCELED
    if job.status == parse_job_service.RecipeParseJobStatus.COMPLETE.value:
        parse_job_service.mark_canceled(db, job)
        return ParseJobStatus(
            id=job.id,
            status=job.status,
            result=None,
            error_code=None,
            error_message=None,
        )
    
    # For other terminal states, don't allow canceling
    if job.status in {
        parse_job_service.RecipeParseJobStatus.ERROR.value,
        parse_job_service.RecipeParseJobStatus.COMMITTED.value,
        parse_job_service.RecipeParseJobStatus.ABANDONED.value,
        parse_job_service.RecipeParseJobStatus.CANCELED.value,
    }:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Job cannot be canceled")
    
    ok = parse_job_service.mark_canceled(db, job)
    if not ok:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Job cannot be canceled")
    return ParseJobStatus(
        id=job.id,
        status=job.status,
        result=None,
        error_code=None,
        error_message=None,
    )

