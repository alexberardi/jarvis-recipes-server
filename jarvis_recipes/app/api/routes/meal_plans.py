import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from jarvis_recipes.app.api.deps import get_current_user, get_db_session
from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.schemas.meal_plan import MealPlanGenerateRequest
from jarvis_recipes.app.services import parse_job_service

router = APIRouter(prefix="/meal-plans", tags=["meal_plans"])


@router.post("/generate/jobs", status_code=202)
def enqueue_meal_plan_generation(
    payload: MealPlanGenerateRequest,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    request_id = str(uuid.uuid4())
    try:
        job = parse_job_service.create_job(
            db=db,
            user_id=str(current_user.id),
            job_type="meal_plan_generate",
            job_data={"request_id": request_id, "payload": payload.model_dump(mode="json")},
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))
    return {"job_id": job.id, "request_id": request_id}


@router.get("/generate/jobs/{job_id}")
def get_meal_plan_job(
    job_id: str,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    job = parse_job_service.get_job_for_user(db, job_id, current_user.id)
    if not job or job.job_type != "meal_plan_generate":
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "id": job.id,
        "status": job.status,
        "result": job.result_json,
        "error_code": job.error_code,
        "error_message": job.error_message,
    }

