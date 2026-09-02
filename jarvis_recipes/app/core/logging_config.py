"""Console + remote logging setup.

The API process and the worker entrypoints share this so a log line looks the
same whichever process emitted it, and so every process ships to jarvis-logs.

Remote shipping is best effort: no jarvis-log-client installed or no
JARVIS_APP_KEY means console-only, never a hard failure.
"""

import logging
import os

# Matches the name registered in jarvis-config-service known_services, so the
# Loki label lines up with the service everywhere else in the stack.
SERVICE_NAME = "jarvis-recipes-server"

_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")

# Even at DEBUG these swamp the real signal with per-request chatter.
_NOISY_LOGGERS = ("httpx", "httpcore", "urllib3", "sqlalchemy.engine")

logger = logging.getLogger(__name__)

_remote_handler: logging.Handler | None = None


def setup_console_logging() -> None:
    """Configure root console logging from JARVIS_LOG_CONSOLE_LEVEL (default INFO).

    Uvicorn's default config only touches its own loggers, so without this the
    root logger has no handler and every `getLogger(__name__)` call in this
    package is dropped by logging.lastResort.
    """
    level_name = os.getenv("JARVIS_LOG_CONSOLE_LEVEL", "INFO")
    level = getattr(logging, level_name.upper(), logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # basicConfig leaves handler levels at NOTSET; pin the console handler so a
    # more verbose remote level can widen the root logger later without also
    # making the console verbose.
    for handler in logging.getLogger().handlers:
        handler.setLevel(level)

    for noisy in _NOISY_LOGGERS:
        logging.getLogger(noisy).setLevel(logging.WARNING)


def setup_remote_logging() -> None:
    """Attach a jarvis-logs handler at JARVIS_LOG_REMOTE_LEVEL (default INFO)."""
    global _remote_handler

    if _remote_handler is not None:
        return

    try:
        from jarvis_log_client import JarvisLogHandler, init as init_log_client
    except ImportError:
        logger.debug("jarvis-log-client not installed, remote logging disabled")
        return

    app_key = os.getenv("JARVIS_APP_KEY")
    if not app_key:
        logger.warning("JARVIS_APP_KEY not set, remote logging disabled")
        return

    init_log_client(app_id=os.getenv("JARVIS_APP_ID", SERVICE_NAME), app_key=app_key)

    level_name = os.getenv("JARVIS_LOG_REMOTE_LEVEL", "INFO")
    level = getattr(logging, level_name.upper(), logging.INFO)
    _remote_handler = JarvisLogHandler(service=SERVICE_NAME, level=level)

    root = logging.getLogger()
    if root.level == logging.NOTSET or level < root.level:
        root.setLevel(level)
    root.addHandler(_remote_handler)

    # This package logs via getLogger(__name__), which reaches the root logger,
    # but uvicorn's own loggers do not propagate — they need the handler too.
    for name in _UVICORN_LOGGERS:
        logging.getLogger(name).addHandler(_remote_handler)

    logger.info("Remote logging enabled to jarvis-logs (level=%s)", level_name)


def shutdown_remote_logging() -> None:
    """Detach and flush the remote handler so a short-lived process loses nothing."""
    global _remote_handler

    if _remote_handler is None:
        return

    logging.getLogger().removeHandler(_remote_handler)
    for name in _UVICORN_LOGGERS:
        logging.getLogger(name).removeHandler(_remote_handler)
    _remote_handler.close()
    _remote_handler = None
