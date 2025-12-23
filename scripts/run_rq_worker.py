#!/usr/bin/env python
"""
RQ worker for processing recipe parsing and meal planning jobs from Redis.

This replaces the polling-based worker with a Redis Queue (RQ) worker.
Run with:
    rq worker --url redis://localhost:6379 url image ingestion meal_plan_generate

Or use this script which sets up the worker with proper configuration:
    python scripts/run_rq_worker.py
"""
import logging
import signal
import sys
import time

from rq import Worker
from rq.connections import push_connection

from jarvis_recipes.app.core.config import get_settings
from jarvis_recipes.app.services.queue_service import get_redis_connection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rq_worker")

# Queue names to listen to (per PRD queue-flow.md)
from jarvis_recipes.app.services.queue_service import QUEUE_RECIPES
QUEUE_NAMES = [QUEUE_RECIPES]  # Listen to jarvis.recipes.jobs


def setup_cleanup():
    """Setup cleanup handlers for graceful shutdown."""
    def signal_handler(sig, frame):
        logger.info("Received signal %s, shutting down gracefully", sig)
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


def main():
    """Start RQ worker with graceful error handling."""
    settings = get_settings()
    logger.info("Starting RQ worker for queues: %s", ", ".join(QUEUE_NAMES))
    logger.info("Redis connection: %s:%s", settings.redis_host, settings.redis_port)
    
    max_restarts = 10
    restart_count = 0
    
    while restart_count < max_restarts:
        try:
            # Get Redis connection
            redis_conn = get_redis_connection()
            push_connection(redis_conn)
            
            # Get queues
            from jarvis_recipes.app.services.queue_service import get_queue
            queues = [get_queue(name) for name in QUEUE_NAMES]
            
            # Create and start worker
            worker = Worker(queues, connection=redis_conn)
            logger.info("Worker started successfully")
            worker.work(with_scheduler=True)  # with_scheduler enables job cleanup
            # If worker.work() returns normally, exit gracefully
            logger.info("Worker stopped normally")
            break
        except KeyboardInterrupt:
            logger.info("Worker interrupted by user")
            break
        except Exception as exc:
            restart_count += 1
            logger.exception("Worker crashed (restart %d/%d): %s", restart_count, max_restarts, exc)
            if restart_count >= max_restarts:
                logger.error("Worker exceeded max restarts (%d), exiting", max_restarts)
                raise
            # Wait a bit before restarting
            import time
            time.sleep(5)
            logger.info("Restarting worker...")


if __name__ == "__main__":
    setup_cleanup()
    main()

