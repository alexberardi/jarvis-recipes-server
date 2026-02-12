#!/usr/bin/env python
"""
Periodic cleanup task for abandoned jobs and expired stage recipes.

This can be run as a cron job or separate process to clean up stale jobs.
"""
import logging

from jarvis_recipes.app.core.config import get_settings
from jarvis_recipes.app.db.session import SessionLocal
from jarvis_recipes.app.services import meal_plan_service, parse_job_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cleanup")


def run_cleanup():
    """Run cleanup tasks."""
    settings = get_settings()
    with SessionLocal() as db:
        try:
            abandoned = parse_job_service.abandon_stale_jobs(db, settings.recipe_parse_job_abandon_minutes)
            if abandoned:
                logger.info("Marked %s jobs as ABANDONED", abandoned)
            cleaned, abandoned_jobs = meal_plan_service.cleanup_expired_stage_recipes(db, cutoff_hours=72, mark_jobs=True)
            if cleaned:
                logger.info("Deleted %s expired stage recipes (abandoned %s jobs)", cleaned, abandoned_jobs)
        except (OSError, RuntimeError):
            logger.exception("Cleanup failed")


if __name__ == "__main__":
    run_cleanup()

