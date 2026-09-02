#!/usr/bin/env python
"""
Periodic cleanup task for abandoned jobs and expired stage recipes.

This can be run as a cron job or separate process to clean up stale jobs.
"""
import logging

from jarvis_recipes.app.core.logging_config import (
    setup_console_logging,
    setup_remote_logging,
    shutdown_remote_logging,
)
from jarvis_recipes.app.db.session import SessionLocal
from jarvis_recipes.app.services import meal_plan_service, parse_job_service
from jarvis_recipes.app.services.settings_service import get_settings_service

setup_console_logging()
setup_remote_logging()
logger = logging.getLogger("cleanup")


def run_cleanup():
    """Run cleanup tasks."""
    abandon_minutes = get_settings_service().get_int("parse_job.abandon_minutes", 4320)
    with SessionLocal() as db:
        try:
            abandoned = parse_job_service.abandon_stale_jobs(db, abandon_minutes)
            if abandoned:
                logger.info("Marked %s jobs as ABANDONED", abandoned)
            cleaned, abandoned_jobs = meal_plan_service.cleanup_expired_stage_recipes(db, cutoff_hours=72, mark_jobs=True)
            if cleaned:
                logger.info("Deleted %s expired stage recipes (abandoned %s jobs)", cleaned, abandoned_jobs)
        except (OSError, RuntimeError):
            logger.exception("Cleanup failed")


if __name__ == "__main__":
    try:
        run_cleanup()
    finally:
        # Short-lived process: flush the batch before the interpreter exits.
        shutdown_remote_logging()

