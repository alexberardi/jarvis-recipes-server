"""Settings service for jarvis-recipes-server.

Provides runtime configuration that can be modified without restarting.
Settings are stored in the database with fallback to environment variables.
Uses the shared jarvis-settings-client library.

Only keys with a real consumer belong here — a definition with no reader shows
up as an editable knob in jarvis-admin that silently does nothing. Every key
below is read by the call sites named in its comment.
"""

import logging

from jarvis_settings_client import SettingDefinition, SettingsService

logger = logging.getLogger(__name__)


# Default browser UA for outbound recipe-site scraping. Long enough to be worth
# a name; kept identical to the historical SCRAPER_USER_AGENT default so an
# unseeded deployment fetches exactly as it did before.
DEFAULT_SCRAPER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

DEFAULT_IMAGE_MAX_BYTES = 10 * 1024 * 1024


# Recipes settings definitions
# NOTE: `auth.algorithm` was removed 2026-08-23. It only ever gated JWT
# *verification* here, and a verifier has to accept HS256 and RS256 together
# during the RS256 migration — so a single configured algorithm is exactly the
# wrong control. What jarvis-auth MINTS is jarvis-auth's setting, not this
# service's. The seeded row in alembic b2c3d4e5f6g7 is now inert; harmless, but
# worth dropping next time this service's settings are migrated.
SETTINGS_DEFINITIONS: list[SettingDefinition] = [
    # Read by services/llm_client.py (URL/text extraction, draft cleanup) and
    # services/url_parsing/extractors/llm.py.
    SettingDefinition(
        key="llm.full_model_name",
        category="llm",
        value_type="string",
        default="live",
        description="Model name used for full recipe extraction and meal planning",
        env_fallback="JARVIS_FULL_MODEL_NAME",
    ),
    # Read by services/image_ingest_pipeline.py and services/queue_worker.py for
    # the cheaper OCR-text structuring pass.
    SettingDefinition(
        key="llm.lightweight_model_name",
        category="llm",
        value_type="string",
        default="live",
        description="Model name used for cheap OCR-text structuring passes",
        env_fallback="JARVIS_LIGHTWEIGHT_MODEL_NAME",
    ),
    # Read by services/llm_client.match_grocery_items. "background" is a routing
    # alias the proxy understands directly (api/chat_routes.py), not a filename:
    # it runs on the second model slot, which is the slower, larger one and is not
    # holding the interactive session's context.
    SettingDefinition(
        key="llm.background_model_name",
        category="llm",
        value_type="string",
        default="background",
        description="Model name used for slow background passes (grocery SKU matching)",
        env_fallback="JARVIS_BACKGROUND_MODEL_NAME",
    ),
    # Read by services/queue_worker.py and scripts/run_parse_worker.py.
    SettingDefinition(
        key="queue.max_retries",
        category="queue",
        value_type="int",
        default=3,
        description="Maximum retries for a failed recipe parse job",
        env_fallback="LLM_RECIPE_QUEUE_MAX_RETRIES",
    ),
    # Read by api/routes/recipes.py, scripts/run_cleanup.py and
    # scripts/run_parse_worker.py to reap jobs stuck in-progress.
    SettingDefinition(
        key="parse_job.abandon_minutes",
        category="parse_job",
        value_type="int",
        default=4320,
        description="Minutes before an in-progress parse job is considered abandoned",
        env_fallback="RECIPE_PARSE_JOB_ABANDON_MINUTES",
    ),
    # Read by api/routes/from_image.py to reject oversized uploads.
    SettingDefinition(
        key="image.max_bytes",
        category="image",
        value_type="int",
        default=DEFAULT_IMAGE_MAX_BYTES,
        description="Maximum accepted size of a single uploaded recipe image, in bytes",
        env_fallback="RECIPE_IMAGE_MAX_BYTES",
    ),
    # Read by services/url_parsing/html_fetcher.py.
    SettingDefinition(
        key="scraper.user_agent",
        category="scraper",
        value_type="string",
        default=DEFAULT_SCRAPER_USER_AGENT,
        description="User-Agent header sent when fetching recipe pages",
        env_fallback="SCRAPER_USER_AGENT",
    ),
]


# Global singleton
_settings_service: SettingsService | None = None


def get_settings_service() -> SettingsService:
    """Get the global SettingsService instance."""
    global _settings_service
    if _settings_service is None:
        from jarvis_recipes.app.db.models import Setting
        from jarvis_recipes.app.db.session import SessionLocal

        _settings_service = SettingsService(
            definitions=SETTINGS_DEFINITIONS,
            get_db_session=SessionLocal,
            setting_model=Setting,
        )
    return _settings_service


def reset_settings_service() -> None:
    """Reset the settings service singleton (for testing)."""
    global _settings_service
    _settings_service = None
