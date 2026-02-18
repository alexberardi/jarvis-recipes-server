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
    llm_lightweight_model_name: str = Field("lightweight", alias="JARVIS_LIGHTWEIGHT_MODEL_NAME")
    llm_recipe_queue_max_retries: int = Field(3, alias="LLM_RECIPE_QUEUE_MAX_RETRIES")
    jarvis_app_id: str | None = Field(None, alias="JARVIS_APP_ID")
    jarvis_app_key: str | None = Field(None, alias="JARVIS_APP_KEY")
    recipe_parse_job_abandon_minutes: int = Field(4320, alias="RECIPE_PARSE_JOB_ABANDON_MINUTES")
    recipe_image_max_bytes: int = Field(10 * 1024 * 1024, alias="RECIPE_IMAGE_MAX_BYTES")
    recipe_ocr_tier_max: int = Field(1, alias="RECIPE_OCR_TIER_MAX")  # Only OCR tier now (vision/cloud handled by OCR service)
    scraper_user_agent: str = Field(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        alias="SCRAPER_USER_AGENT",
    )
    scraper_cookies: str | None = Field(None, alias="SCRAPER_COOKIES")
    # Legacy S3 config (kept for backwards compatibility)
    recipe_image_s3_bucket: str | None = Field(None, alias="RECIPE_IMAGE_S3_BUCKET")
    recipe_image_s3_region: str | None = Field(None, alias="RECIPE_IMAGE_S3_REGION")
    recipe_image_s3_prefix: str = Field("recipe-images", alias="RECIPE_IMAGE_S3_PREFIX")
    recipe_image_s3_presign_ttl_seconds: int = Field(
        3600, alias="RECIPE_IMAGE_S3_PRESIGN_TTL_SECONDS"
    )
    
    # New unified object store config (per PRD s3-to-minio.md)
    object_store_provider: str = Field("minio", alias="OBJECT_STORE_PROVIDER")  # "minio" or "s3"
    s3_endpoint_url: str | None = Field(None, alias="S3_ENDPOINT_URL")  # Required for MinIO
    s3_region: str = Field("us-east-1", alias="S3_REGION")
    s3_force_path_style: bool = Field(True, alias="S3_FORCE_PATH_STYLE")  # True for MinIO, False for AWS
    s3_bucket: str | None = Field(None, alias="S3_BUCKET")  # e.g., "jarvis-dev"
    
    # AWS credentials (used for both AWS S3 and MinIO)
    aws_access_key_id: str | None = Field(None, alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: str | None = Field(None, alias="AWS_SECRET_ACCESS_KEY")
    aws_session_token: str | None = Field(None, alias="AWS_SESSION_TOKEN")
    jarvis_ocr_service_url: str | None = Field(None, alias="JARVIS_OCR_SERVICE_URL")
    # OCR service uses same auth as LLM proxy
    # (jarvis_app_id and jarvis_app_key are reused)
    redis_host: str = Field("localhost", alias="REDIS_HOST")
    redis_port: int = Field(6379, alias="REDIS_PORT")
    redis_password: str | None = Field(None, alias="REDIS_PASSWORD")

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

