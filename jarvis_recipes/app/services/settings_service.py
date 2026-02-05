"""Settings service for jarvis-recipes-server.

Provides runtime configuration that can be modified without restarting.
Settings are stored in the database with fallback to environment variables.
Uses the shared jarvis-settings-client library.
"""

import logging

from jarvis_settings_client import SettingDefinition, SettingsService

logger = logging.getLogger(__name__)


# Recipes settings definitions
SETTINGS_DEFINITIONS: list[SettingDefinition] = [
    SettingDefinition(
        key="auth.algorithm",
        category="auth",
        value_type="string",
        default="HS256",
        description="JWT signing algorithm",
        env_fallback="AUTH_ALGORITHM",
    ),
    SettingDefinition(
        key="parser.timeout_seconds",
        category="parser",
        value_type="int",
        default=30,
        description="Timeout for recipe URL parsing in seconds",
        env_fallback="PARSER_TIMEOUT",
    ),
    SettingDefinition(
        key="parser.max_retries",
        category="parser",
        value_type="int",
        default=3,
        description="Maximum retries for recipe URL parsing",
        env_fallback="PARSER_MAX_RETRIES",
    ),
    SettingDefinition(
        key="parser.use_llm_fallback",
        category="parser",
        value_type="bool",
        default=True,
        description="Whether to use LLM as fallback for recipe parsing",
        env_fallback="PARSER_USE_LLM_FALLBACK",
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
