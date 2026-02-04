"""
Service discovery configuration for jarvis-recipes-server.

Fetches service URLs from jarvis-config-service at startup,
with fallback to environment variables.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_initialized = False

# Service name to default URL mapping
_DEFAULTS = {
    "jarvis-ocr": "http://localhost:5009",
}

# Service name to env var fallback
_ENV_VAR_FALLBACKS = {
    "jarvis-ocr": "JARVIS_OCR_SERVICE_URL",
}


def init() -> bool:
    """
    Initialize service discovery from jarvis-config-service.

    Returns True if successful, False if falling back to env vars.
    """
    global _initialized

    config_url = os.getenv("JARVIS_CONFIG_URL")
    if not config_url:
        logger.warning("JARVIS_CONFIG_URL not set - using env vars for service URLs")
        return False

    try:
        from jarvis_config_client import init as init_config_client

        success = init_config_client(config_url=config_url)
        if success:
            _initialized = True
            logger.info("Service discovery initialized from %s", config_url)
            return True
        else:
            logger.warning("Config service unavailable - using env vars")
            return False

    except ImportError:
        logger.warning("jarvis-config-client not installed - using env vars")
        return False
    except (OSError, RuntimeError) as e:
        logger.error("Failed to initialize service discovery: %s", e)
        return False


def is_initialized() -> bool:
    """Check if service discovery is initialized."""
    return _initialized


def _get_url(service_name: str) -> str:
    """Get URL for a service, with fallback chain."""
    # Try config client first
    if _initialized:
        try:
            from jarvis_config_client import get_service_url
            url = get_service_url(service_name)
            if url:
                return url
        except (ImportError, OSError, RuntimeError, KeyError):
            pass

    # Fall back to env var
    env_var = _ENV_VAR_FALLBACKS.get(service_name)
    if env_var:
        url = os.getenv(env_var)
        if url:
            return url

    # Fall back to default
    return _DEFAULTS.get(service_name, "")


def get_ocr_url() -> str:
    """Get jarvis-ocr service URL."""
    return _get_url("jarvis-ocr")
