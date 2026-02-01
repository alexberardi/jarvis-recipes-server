import logging
import uuid

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette import status

from jarvis_recipes.app.api.routes import api_router
from jarvis_recipes.app.core.config import get_settings
from jarvis_recipes.app.core import service_config

logger = logging.getLogger(__name__)


async def validation_exception_handler(request, exc: RequestValidationError):
    job_id = str(uuid.uuid4())
    details = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", []) if part is not None)
        msg = err.get("msg", "Invalid value")
        details.append({"field": loc or None, "message": msg})
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error_code": "validation_error",
            "message": "Invalid request payload.",
            "details": details,
            "job_id": job_id,
        },
    )


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Jarvis Recipes", version="0.1.0")
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.include_router(api_router)
    app.mount("/media", StaticFiles(directory=settings.media_root), name="media")

    @app.on_event("startup")
    async def startup_event() -> None:
        if service_config.init():
            logger.info("Service discovery initialized")
        else:
            logger.info("Using environment variables for service URLs")

    return app


app = create_app()

