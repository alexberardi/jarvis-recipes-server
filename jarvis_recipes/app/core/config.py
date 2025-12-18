import logging
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = Field("sqlite:///./jarvis.db", alias="DATABASE_URL")
    auth_secret_key: str = Field("change-me", alias="AUTH_SECRET_KEY")
    auth_algorithm: str = Field("HS256", alias="AUTH_ALGORITHM")
    media_root: Path = Path("media")
    admin_secret: str = Field("admin-secret", alias="ADMIN_SECRET")
    llm_base_url: str | None = Field(None, alias="LLM_BASE_URL")
    llm_full_model_name: str = Field("full", alias="JARVIS_FULL_MODEL_NAME")
    llm_vision_model_name: str = Field("vision", alias="JARVIS_VISION_MODEL_NAME")
    llm_recipe_queue_max_retries: int = Field(3, alias="LLM_RECIPE_QUEUE_MAX_RETRIES")
    llm_app_id: str | None = Field(None, alias="LLM_APP_ID")
    llm_app_key: str | None = Field(None, alias="LLM_APP_KEY")
    recipe_parse_job_abandon_minutes: int = Field(4320, alias="RECIPE_PARSE_JOB_ABANDON_MINUTES")
    recipe_image_max_bytes: int = Field(10 * 1024 * 1024, alias="RECIPE_IMAGE_MAX_BYTES")
    recipe_ocr_tier_max: int = Field(3, alias="RECIPE_OCR_TIER_MAX")
    recipe_ocr_tesseract_enabled: bool = Field(True, alias="RECIPE_OCR_TESSERACT_ENABLED")
    recipe_ocr_tier2_enabled: bool = Field(False, alias="RECIPE_OCR_TIER2_ENABLED")
    recipe_ocr_vision_enabled: bool = Field(True, alias="RECIPE_OCR_VISION_ENABLED")
    recipe_ocr_easyocr_gpu: bool = Field(False, alias="RECIPE_OCR_EASYOCR_GPU")
    # Vision subprocess controls (for macOS memory stability)
    vision_subprocess_enabled: bool = Field(True, alias="JARVIS_VISION_SUBPROCESS_ENABLED")
    vision_timeout_seconds: int = Field(180, alias="JARVIS_VISION_TIMEOUT_SECONDS")
    vision_subprocess_max_retries: int = Field(1, alias="JARVIS_VISION_SUBPROCESS_MAX_RETRIES")
    scraper_user_agent: str = Field(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        alias="SCRAPER_USER_AGENT",
    )
    scraper_cookies: str | None = Field(None, alias="SCRAPER_COOKIES")
    recipe_image_s3_bucket: str | None = Field(None, alias="RECIPE_IMAGE_S3_BUCKET")
    recipe_image_s3_region: str | None = Field(None, alias="RECIPE_IMAGE_S3_REGION")
    recipe_image_s3_prefix: str = Field("recipe-images", alias="RECIPE_IMAGE_S3_PREFIX")
    recipe_image_s3_presign_ttl_seconds: int = Field(
        3600, alias="RECIPE_IMAGE_S3_PRESIGN_TTL_SECONDS"
    )
    aws_access_key_id: str | None = Field(None, alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: str | None = Field(None, alias="AWS_SECRET_ACCESS_KEY")
    aws_session_token: str | None = Field(None, alias="AWS_SESSION_TOKEN")

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")


logger = logging.getLogger(__name__)


@lru_cache
def get_settings() -> Settings:
    try:
        settings = Settings()
    except PermissionError:
        logger.warning("Unable to read .env; continuing with environment variables only")
        settings = Settings(_env_file=None)
    settings.media_root.mkdir(parents=True, exist_ok=True)
    return settings

