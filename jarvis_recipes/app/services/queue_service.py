"""
Redis queue service for recipe parsing and meal planning jobs.

This module provides a Redis-based queue using RQ (Redis Queue) with
the baton-pass workflow pattern. Jobs use a standardized envelope format
and are routed to appropriate queues (jarvis.recipes.jobs or jarvis.ocr.jobs).
"""
import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from redis import Redis
from rq import Queue

from jarvis_recipes.app.core.config import get_settings

logger = logging.getLogger(__name__)

# Queue names per PRD
QUEUE_RECIPES = "jarvis.recipes.jobs"
QUEUE_OCR = "jarvis.ocr.jobs"

# Global Redis connection and queues
_redis_conn: Optional[Redis] = None
_queues: Dict[str, Queue] = {}


def get_redis_connection() -> Redis:
    """Get or create Redis connection."""
    global _redis_conn
    if _redis_conn is None:
        settings = get_settings()
        _redis_conn = Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            decode_responses=False,  # RQ expects bytes
        )
    return _redis_conn


def get_queue(queue_name: str) -> Queue:
    """Get or create a named queue."""
    global _queues
    if queue_name not in _queues:
        conn = get_redis_connection()
        _queues[queue_name] = Queue(queue_name, connection=conn)
    return _queues[queue_name]


def create_envelope(
    job_type: str,
    job_id: str,
    workflow_id: str,
    source: str,
    target: str,
    payload: Dict[str, Any],
    reply_to: Optional[str] = None,
    parent_job_id: Optional[str] = None,
    request_id: Optional[str] = None,
    attempt: int = 1,
) -> Dict[str, Any]:
    """
    Create a queue message envelope per PRD specification.
    
    Args:
        job_type: Type of job (e.g., "ocr.extract_text.requested", "ocr.completed", "recipe.import.url.requested")
        job_id: Unique job identifier
        workflow_id: Workflow identifier (can be same as job_id for simple workflows)
        source: Source service name
        target: Target service name
        payload: Job-specific payload data
        reply_to: Optional queue name to send completion events to
        parent_job_id: Optional parent job ID for traceability
        request_id: Optional request ID for tracing
        attempt: Attempt number (default 1)
    
    Returns:
        Envelope dictionary matching PRD format
    """
    return {
        "schema_version": 1,
        "job_id": job_id,
        "workflow_id": workflow_id,
        "job_type": job_type,
        "source": source,
        "target": target,
        "created_at": datetime.utcnow().isoformat(),
        "attempt": attempt,
        "reply_to": reply_to,
        "payload": payload,
        "trace": {
            "request_id": request_id,
            "parent_job_id": parent_job_id,
        },
    }


def enqueue_ocr_request(
    workflow_id: str,
    job_id: str,
    image_refs: list[Dict[str, Any]],  # List of {"kind": "s3", "value": "s3://bucket/key", "index": 0}
    options: Optional[Dict[str, Any]] = None,
    request_id: Optional[str] = None,
) -> None:
    """
    Enqueue an OCR extraction request directly to the OCR service queue.
    
    This implements the fast-path routing: image requests go directly to OCR queue.
    Uses raw Redis operations (not RQ) since OCR service is a separate microservice.
    
    Args:
        workflow_id: Workflow identifier
        job_id: Job identifier (typically RecipeParseJob.id)
        image_refs: List of image references with kind, value (full URI), and index
                   (e.g., [{"kind": "s3", "value": "s3://bucket/key", "index": 0}, ...])
        options: Optional OCR options (language, etc.)
        request_id: Optional request ID for tracing
    """
    try:
        payload = {
            "image_refs": image_refs,  # Array of image_ref objects with kind, value (full URI), and index
            "options": options or {"language": "en"},
        }
        
        envelope = create_envelope(
            job_type="ocr.extract_text.requested",
            job_id=job_id,
            workflow_id=workflow_id,
            source="jarvis-recipes-server",
            target="jarvis-ocr-service",
            payload=payload,
            reply_to=QUEUE_RECIPES,
            request_id=request_id,
        )
        
        # Use raw Redis LPUSH for cross-service queue (per PRD: "V1 can use Redis Lists")
        # OCR service will consume from this queue using its own worker
        conn = get_redis_connection()
        envelope_json = json.dumps(envelope)
        conn.lpush(QUEUE_OCR, envelope_json.encode('utf-8'))
        
        logger.info("Enqueued OCR request %s (workflow %s) to %s", job_id, workflow_id, QUEUE_OCR)
    except Exception as exc:
        logger.exception("Failed to enqueue OCR request %s: %s", job_id, exc)
        raise


def enqueue_recipes_job(
    job_type: str,
    job_id: str,
    workflow_id: str,
    job_data: Dict[str, Any],
    request_id: Optional[str] = None,
    parent_job_id: Optional[str] = None,
) -> None:
    """
    Enqueue a job to the recipes queue.
    
    Args:
        job_type: Type of job ("recipe.import.url.requested", "recipe.create.manual.requested", "ocr.completed", etc.)
        job_id: Unique job identifier
        workflow_id: Workflow identifier
        job_data: Job payload data
        request_id: Optional request ID for tracing
        parent_job_id: Optional parent job ID (for OCR completion events)
    """
    try:
        envelope = create_envelope(
            job_type=job_type,
            job_id=job_id,
            workflow_id=workflow_id,
            source="jarvis-recipes-server",
            target="jarvis-recipes-server",
            payload=job_data,
            reply_to=None,  # Recipes jobs don't reply to other queues
            request_id=request_id,
            parent_job_id=parent_job_id,
        )
        
        queue = get_queue(QUEUE_RECIPES)
        queue.enqueue(
            "jarvis_recipes.app.services.queue_worker.process_job",
            json.dumps(envelope),
            job_id=job_id,
            job_timeout="10m",
        )
        logger.info("Enqueued job %s (%s) to %s", job_id, job_type, QUEUE_RECIPES)
    except Exception as exc:
        logger.exception("Failed to enqueue job %s: %s", job_id, exc)
        raise


def enqueue_ocr_completion(
    workflow_id: str,
    job_id: str,
    payload: Dict[str, Any],
    parent_job_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> None:
    """
    Enqueue an OCR completion event to the recipes queue.
    
    This function should be called by the OCR service to properly enqueue
    OCR completion events using RQ. The OCR service can import and use this
    function, or replicate its logic using RQ's enqueue method.
    
    IMPORTANT: The OCR service must use RQ's queue.enqueue() method, NOT raw Redis LPUSH.
    The function path must be exactly: "jarvis_recipes.app.services.queue_worker.process_job"
    The queue name must be exactly: "jarvis.recipes.jobs"
    
    Example usage from OCR service:
        from rq import Queue
        from redis import Redis
        import json
        
        redis_conn = Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=False)
        queue = Queue("jarvis.recipes.jobs", connection=redis_conn)
        
        envelope = {
            "schema_version": 1,
            "job_id": job_id,
            "workflow_id": workflow_id,
            "job_type": "ocr.completed",
            "source": "jarvis-ocr-service",
            "target": "jarvis-recipes-server",
            "created_at": datetime.utcnow().isoformat(),
            "attempt": 1,
            "reply_to": None,
            "payload": payload,
            "trace": {
                "request_id": request_id,
                "parent_job_id": parent_job_id or workflow_id,
            },
        }
        
        queue.enqueue(
            "jarvis_recipes.app.services.queue_worker.process_job",
            json.dumps(envelope),
            job_id=job_id,
            job_timeout="10m",
        )
    
    Args:
        workflow_id: Workflow identifier (original job ID)
        job_id: New job ID for this completion event
        payload: OCR completion payload (status, results, error, etc.)
        parent_job_id: Parent job ID (typically same as workflow_id)
        request_id: Optional request ID for tracing
    """
    enqueue_recipes_job(
        job_type="ocr.completed",
        job_id=job_id,
        workflow_id=workflow_id,
        job_data=payload,
        request_id=request_id,
        parent_job_id=parent_job_id or workflow_id,
    )


def enqueue_job(
    job_type: str,
    job_id: str,
    job_data: Dict[str, Any],
    queue_name: Optional[str] = None,
) -> None:
    """
    Legacy enqueue function for backwards compatibility.
    
    For image jobs, this should route to OCR queue. For others, routes to recipes queue.
    """
    # Map old job types to new queue routing
    if job_type == "image":
        # Image jobs should use enqueue_ocr_request, but for now route to recipes
        # This will be updated when we fully migrate
        logger.warning("Legacy image job enqueue - should use enqueue_ocr_request")
        enqueue_recipes_job(job_type, job_id, job_id, job_data)
    else:
        enqueue_recipes_job(job_type, job_id, job_id, job_data)


def get_queue_length(queue_name: str) -> int:
    """Get the number of pending jobs in a queue."""
    try:
        queue = get_queue(queue_name)
        return len(queue)
    except Exception as exc:
        logger.warning("Failed to get queue length for %s: %s", queue_name, exc)
        return 0

