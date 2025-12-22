import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from jarvis_recipes.app.api.deps import get_current_user, get_db_session
from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.schemas.ingestion_input import IngestionInput
from jarvis_recipes.app.services import parse_job_service
from jarvis_recipes.app.services.url_recipe_parser import preflight_validate_url


router = APIRouter(tags=["recipes"])


class UrlRequest(BaseModel):
    url: str


class PayloadRequest(BaseModel):
    input: IngestionInput


@router.post("/recipes/parse-url/async")
async def parse_url_async(
    payload: UrlRequest,
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
    preflight = await preflight_validate_url(payload.url)
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
    return {
        "id": job_id,
        "status": "PENDING",
        "next_action": "webview_extract",
        "next_action_reason": "webview_required",
    }


@router.post("/recipes/parse-payload/async")
def parse_payload_async(
    payload: PayloadRequest,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    job = parse_job_service.create_ingestion_job(db, str(current_user.id), payload.input.model_dump())
    return {"id": job.id, "status": job.status}

