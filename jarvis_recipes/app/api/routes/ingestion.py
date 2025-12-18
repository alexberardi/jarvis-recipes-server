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
    job_id = str(uuid.uuid4())
    preflight = await preflight_validate_url(payload.url)
    if not preflight.ok:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": preflight.error_code or "preflight_failed",
                "message": preflight.error_message,
                "status_code": preflight.status_code,
                "job_id": job_id,
            },
        )
    job = parse_job_service.create_ingestion_job(
        db,
        str(current_user.id),
        {
            "source_type": "server_fetch",
            "source_url": payload.url,
        },
    )
    return {"id": job.id, "status": job.status}


@router.post("/recipes/parse-payload/async")
def parse_payload_async(
    payload: PayloadRequest,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    job = parse_job_service.create_ingestion_job(db, str(current_user.id), payload.input.model_dump())
    return {"id": job.id, "status": job.status}

