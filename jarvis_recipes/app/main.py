import logging
import uuid

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from jarvis_settings_client import create_combined_auth, create_settings_router, create_superuser_auth
from starlette import status

from jarvis_recipes.app.api.deps import verify_app_auth
from jarvis_recipes.app.api.routes import api_router
from jarvis_recipes.app.core import service_config
from jarvis_recipes.app.core.config import enforce_secret_security, get_settings
from jarvis_recipes.app.core.logging_config import setup_console_logging, setup_remote_logging
from jarvis_recipes.app.services.settings_service import get_settings_service

# Before create_app(): the Dockerfile CMD is a bare uvicorn, whose default config
# configures only the uvicorn* loggers, so without this nothing this package logs
# ever reaches a handler.
setup_console_logging()

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
    # Warn (dev) or refuse to boot (production) on placeholder/weak secrets.
    enforce_secret_security(settings, logger)
    app = FastAPI(title="Jarvis Recipes", version="0.1.0")
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.include_router(api_router)
    app.mount("/media", StaticFiles(directory=settings.media_root), name="media")

    # Settings routes (app-to-app auth for reads, superuser JWT for writes).
    # Pass the getter, not a resolved URL: jarvis-settings-client accepts
    # `str | Callable[[], str]` and resolves per request, so discovery has run by
    # the time it is called. A literal here would pin localhost inside a container.
    _settings_router = create_settings_router(
        service=get_settings_service(),
        auth_dependency=create_combined_auth(service_config.get_auth_url),
        write_auth_dependency=create_superuser_auth(service_config.get_auth_url),
    )
    app.include_router(_settings_router, prefix="/settings", tags=["settings"])

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.on_event("startup")
    async def startup_event() -> None:
        if service_config.init():
            logger.info("Service discovery initialized")
        else:
            logger.info("Using environment variables for service URLs")
        setup_remote_logging()

    return app


app = create_app()

