"""Service URL discovery via jarvis-config-client."""

import logging

from jarvis_config_client import (
    init as config_init,
    shutdown as config_shutdown,
    get_auth_url,
    get_llm_proxy_url,
    get_ocr_url,
)

logger = logging.getLogger(__name__)

# Re-export for convenience
get_auth_url = get_auth_url
get_ocr_url = get_ocr_url
get_llm_proxy_url = get_llm_proxy_url

_initialized: bool = False


def init() -> bool:
    """Initialize service discovery. Call at startup."""
    global _initialized

    try:
        success = config_init()
        _initialized = True
        if success:
            logger.info("Service discovery initialized")
        return success
    except RuntimeError as e:
        logger.error("Failed to initialize service discovery: %s", e)
        raise


def shutdown() -> None:
    """Shutdown service discovery."""
    global _initialized
    config_shutdown()
    _initialized = False


def is_initialized() -> bool:
    """Check if service discovery is initialized."""
    return _initialized
