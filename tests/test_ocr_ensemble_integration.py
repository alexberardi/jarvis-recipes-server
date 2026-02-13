"""
Integration test for the full OCR ensemble pipeline.

This test exercises the complete flow:
1. Create RecipeIngestion and RecipeParseJob in database
2. Queue OCR request to Redis (jarvis.ocr.jobs)
3. OCR worker processes with all providers in parallel
4. OCR worker sends completion to recipes queue (jarvis.recipes.jobs)
5. Recipes queue worker processes completion with ensemble LLM
6. Assert recipe draft is created correctly

Requirements (must be running):
- Redis (REDIS_HOST, REDIS_PORT)
- OCR worker (jarvis-ocr-service/worker.py)
- LLM proxy (jarvis-llm-proxy-api)

Run with: pytest tests/test_ocr_ensemble_integration.py -v -s --run-integration
"""

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
import redis
from sqlalchemy.orm import Session

from jarvis_recipes.app.db import models
from jarvis_recipes.app.services import parse_job_service
from jarvis_recipes.app.services.queue_worker import _process_ocr_completed

# Test configuration
REDIS_HOST = os.getenv("REDIS_HOST", "10.0.0.122")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "redis")
OCR_QUEUE = "jarvis.ocr.jobs"
RECIPES_QUEUE = "jarvis.recipes.jobs"

TEST_IMAGES_DIR = Path(__file__).parent.parent / "test_images"

logger = logging.getLogger(__name__)


@pytest.fixture
def redis_client():
    """Create a Redis client for the test."""
    client = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_connect_timeout=5
    )
    # Test connection
    try:
        client.ping()
    except redis.ConnectionError as e:
        pytest.skip(f"Redis not available: {e}")
    yield client
    client.close()


def discover_test_images() -> Dict[str, list]:
    """Discover test image sets."""
    tests = {}
    if not TEST_IMAGES_DIR.exists():
        return tests

    for test_dir in sorted(TEST_IMAGES_DIR.iterdir()):
        if test_dir.is_dir() and test_dir.name.startswith("test"):
            images = []
            for img_file in sorted(test_dir.iterdir()):
                if img_file.suffix.lower() in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
                    images.append(img_file)
            if images:
                tests[test_dir.name] = images

    return tests


def create_ocr_request_message(
    job_id: str,
    workflow_id: str,
    image_paths: List[Path],
    reply_to: str = RECIPES_QUEUE
) -> Dict[str, Any]:
    """Create an OCR request message for the queue."""
    image_refs = []
    for i, img_path in enumerate(image_paths):
        image_refs.append({
            "kind": "local_path",
            "value": str(img_path.absolute()),
            "index": i
        })

    return {
        "schema_version": 1,
        "job_id": str(uuid.uuid4()),  # OCR service creates its own job_id
        "workflow_id": workflow_id,  # This links back to our RecipeParseJob
        "job_type": "ocr.extract_text.requested",
        "source": "jarvis-recipes-server",
        "target": "jarvis-ocr-service",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "attempt": 1,
        "reply_to": reply_to,
        "payload": {
            "image_refs": image_refs,
            "image_count": len(image_refs),
            "options": {
                "language": "en"
            }
        },
        "trace": {
            "request_id": str(uuid.uuid4()),
            "parent_job_id": job_id
        }
    }


def wait_for_ocr_job_completion(
    redis_client: redis.Redis,
    ocr_job_id: str,
    workflow_id: str,
    timeout: int = 120
) -> Optional[Dict[str, Any]]:
    """
    Wait for OCR job to complete by polling job status in Redis.

    The OCR service stores job status at 'ocr_job:{job_id}' keys.
    This approach works regardless of how the completion message is queued
    (RQ vs plain Redis).

    Args:
        redis_client: Redis client
        ocr_job_id: The OCR service job ID (from the request message)
        workflow_id: The workflow ID (links back to RecipeParseJob)
        timeout: Max seconds to wait

    Returns:
        Completion message dict in the format expected by _process_ocr_completed,
        or None if timeout.
    """
    start = time.time()
    job_key = f"ocr_job:{ocr_job_id}"

    logger.info(f"Polling for OCR job completion: {job_key}")

    while time.time() - start < timeout:
        # Check job status
        job_data_raw = redis_client.get(job_key)

        if job_data_raw:
            job_data = json.loads(job_data_raw)
            status = job_data.get("status")

            logger.debug(f"OCR job status: {status}")

            if status == "completed":
                # Job completed - extract result and build completion message
                result = job_data.get("result", {})

                # Build completion message in expected format
                completion = {
                    "schema_version": 1,
                    "job_id": ocr_job_id,
                    "workflow_id": workflow_id,
                    "job_type": "ocr.completed",
                    "payload": result  # result should have {status, results, ...}
                }

                logger.info(f"OCR job completed: {ocr_job_id}")
                return completion

            elif status == "failed":
                # Job failed
                error = job_data.get("error", "Unknown error")
                logger.error(f"OCR job failed: {error}")
                return {
                    "schema_version": 1,
                    "job_id": ocr_job_id,
                    "workflow_id": workflow_id,
                    "job_type": "ocr.completed",
                    "payload": {
                        "status": "error",
                        "error": error
                    }
                }

        # Wait before polling again
        time.sleep(2)

    logger.warning(f"Timeout waiting for OCR job: {ocr_job_id}")
    return None


@pytest.mark.integration
class TestOCREnsembleIntegration:
    """Integration tests for the OCR ensemble pipeline."""

    def test_redis_connection(self, redis_client):
        """Verify Redis is accessible."""
        assert redis_client.ping()
        logger.info(f"Redis connected: {REDIS_HOST}:{REDIS_PORT}")

    def test_test_images_exist(self):
        """Verify test images are available."""
        tests = discover_test_images()
        assert len(tests) > 0, f"No test images found in {TEST_IMAGES_DIR}"
        logger.info(f"Found {len(tests)} test image sets: {list(tests.keys())}")

    def test_full_ocr_pipeline_with_ensemble(self, integration_db: Session, redis_client):
        """
        Full integration test: image -> OCR -> ensemble LLM -> recipe draft.

        This test:
        1. Creates database records
        2. Queues OCR request
        3. Waits for OCR completion (requires OCR worker running)
        4. Processes completion with ensemble LLM
        5. Verifies recipe draft was created
        """
        # Discover test images
        test_images = discover_test_images()
        if not test_images:
            pytest.skip("No test images available")

        # Use test3 (properly oriented single image)
        test_name = "test3"
        if test_name not in test_images:
            test_name = list(test_images.keys())[0]

        images = test_images[test_name]
        logger.info(f"Testing with {test_name}: {[img.name for img in images]}")

        # 1. Create database records
        user_id = "test-user-1"
        ingestion_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())

        # Create User first (foreign key requirement)
        user = models.User(user_id=user_id)
        integration_db.add(user)
        integration_db.flush()

        # Create RecipeIngestion
        ingestion = models.RecipeIngestion(
            id=ingestion_id,
            user_id=user_id,
            status="PENDING",
            image_s3_keys=[str(img) for img in images],  # Store image paths
            tier_max=1,
        )
        integration_db.add(ingestion)

        # Create RecipeParseJob
        job = models.RecipeParseJob(
            id=job_id,
            user_id=user_id,
            job_type="image",
            status=parse_job_service.RecipeParseJobStatus.PENDING.value,
            job_data={"ingestion_id": ingestion_id},
        )
        integration_db.add(job)
        integration_db.commit()

        logger.info(f"Created user={user_id}, ingestion={ingestion_id}, job={job_id}")

        # 2. Queue OCR request
        ocr_message = create_ocr_request_message(
            job_id=job_id,
            workflow_id=job_id,  # Use job_id as workflow_id
            image_paths=images,
            reply_to=RECIPES_QUEUE
        )

        # Capture the OCR job_id for polling
        ocr_job_id = ocr_message["job_id"]

        redis_client.lpush(OCR_QUEUE, json.dumps(ocr_message))
        logger.info(f"Queued OCR request to {OCR_QUEUE} [ocr_job_id={ocr_job_id}]")

        # 3. Wait for OCR completion by polling job status
        logger.info("Waiting for OCR completion (ensure OCR worker is running)...")
        completion = wait_for_ocr_job_completion(redis_client, ocr_job_id, job_id, timeout=120)

        if completion is None:
            pytest.fail(
                "OCR completion not received within timeout. "
                "Ensure OCR worker is running: cd jarvis-ocr-service && python worker.py"
            )

        logger.info(f"Received OCR completion: status={completion.get('payload', {}).get('status')}")

        # Verify OCR results
        payload = completion.get("payload", {})
        assert payload.get("status") == "success", f"OCR failed: {payload}"

        results = payload.get("results", [])
        assert len(results) > 0, "No OCR results returned"

        # Check that we got multiple provider results (ensemble)
        first_result = results[0]
        ocr_results = first_result.get("ocr_results", [])
        logger.info(f"Got {len(ocr_results)} provider results for first image")

        for ocr in ocr_results:
            logger.info(
                f"  - {ocr.get('provider')}: conf={ocr.get('confidence', 0):.2f}, "
                f"len={ocr.get('text_length', 0)}"
            )

        assert len(ocr_results) >= 2, "Expected at least 2 OCR providers"

        # 4. Process OCR completion (this calls ensemble LLM)
        integration_db.refresh(job)
        integration_db.refresh(ingestion)

        logger.info("Processing OCR completion with ensemble LLM...")
        _process_ocr_completed(
            db=integration_db,
            job=job,
            payload=payload,
            parent_job_id=ocr_message["trace"]["parent_job_id"]
        )

        # 5. Verify results
        integration_db.refresh(job)
        integration_db.refresh(ingestion)

        logger.info(f"Job status: {job.status}")
        logger.info(f"Ingestion status: {ingestion.status}")

        # Check job completed successfully
        assert job.status == parse_job_service.RecipeParseJobStatus.COMPLETE.value, \
            f"Job failed: {job.error_code} - {job.error_message}"

        # Check ingestion succeeded
        assert ingestion.status == "SUCCEEDED", \
            f"Ingestion failed: {ingestion.pipeline_json}"

        # Check recipe draft was created
        result_json = job.result_json
        assert result_json is not None, "No result_json on job"

        recipe_draft = result_json.get("recipe_draft")
        assert recipe_draft is not None, "No recipe_draft in result"

        # Verify recipe has required fields
        assert recipe_draft.get("title"), "Recipe missing title"
        assert len(recipe_draft.get("ingredients", [])) > 0, "Recipe missing ingredients"
        assert len(recipe_draft.get("steps", [])) > 0, "Recipe missing steps"

        # Log the result
        logger.info("=" * 60)
        logger.info("RECIPE EXTRACTED SUCCESSFULLY")
        logger.info("=" * 60)
        logger.info(f"Title: {recipe_draft.get('title')}")
        logger.info(f"Ingredients: {len(recipe_draft.get('ingredients', []))}")
        logger.info(f"Steps: {len(recipe_draft.get('steps', []))}")

        # Show first few ingredients
        for ing in recipe_draft.get("ingredients", [])[:5]:
            qty = ing.get("quantity", "")
            unit = ing.get("unit", "")
            name = ing.get("name", "")
            logger.info(f"  - {qty} {unit} {name}".strip())

        logger.info("=" * 60)

    def test_multiple_images_ensemble(self, integration_db: Session, redis_client):
        """Test ensemble with multiple images (e.g., multi-page recipe)."""
        test_images = discover_test_images()

        # Find a test with multiple images
        multi_image_test = None
        for name, images in test_images.items():
            if len(images) > 1:
                multi_image_test = (name, images)
                break

        if not multi_image_test:
            pytest.skip("No multi-image test available")

        test_name, images = multi_image_test
        logger.info(f"Testing multi-image with {test_name}: {len(images)} images")

        # Similar flow as above but with multiple images
        user_id = "test-user-2"
        ingestion_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())

        # Create User first (foreign key requirement)
        user = models.User(user_id=user_id)
        integration_db.add(user)
        integration_db.flush()

        ingestion = models.RecipeIngestion(
            id=ingestion_id,
            user_id=user_id,
            status="PENDING",
            image_s3_keys=[str(img) for img in images],
            tier_max=1,
        )
        integration_db.add(ingestion)

        job = models.RecipeParseJob(
            id=job_id,
            user_id=user_id,
            job_type="image",
            status=parse_job_service.RecipeParseJobStatus.PENDING.value,
            job_data={"ingestion_id": ingestion_id},
        )
        integration_db.add(job)
        integration_db.commit()

        # Queue OCR request
        ocr_message = create_ocr_request_message(
            job_id=job_id,
            workflow_id=job_id,
            image_paths=images,
            reply_to=RECIPES_QUEUE
        )

        # Capture the OCR job_id for polling
        ocr_job_id = ocr_message["job_id"]

        redis_client.lpush(OCR_QUEUE, json.dumps(ocr_message))
        logger.info(f"Queued OCR request for {len(images)} images [ocr_job_id={ocr_job_id}]")

        # Wait for completion by polling job status
        completion = wait_for_ocr_job_completion(redis_client, ocr_job_id, job_id, timeout=180)

        if completion is None:
            pytest.fail("OCR completion not received within timeout")

        payload = completion.get("payload", {})
        assert payload.get("status") == "success"

        results = payload.get("results", [])
        assert len(results) == len(images), f"Expected {len(images)} results, got {len(results)}"

        # Process with ensemble LLM
        integration_db.refresh(job)
        integration_db.refresh(ingestion)

        _process_ocr_completed(
            db=integration_db,
            job=job,
            payload=payload,
            parent_job_id=ocr_message["trace"]["parent_job_id"]
        )

        integration_db.refresh(job)

        assert job.status == parse_job_service.RecipeParseJobStatus.COMPLETE.value, \
            f"Job failed: {job.error_code} - {job.error_message}"

        recipe_draft = job.result_json.get("recipe_draft")
        logger.info(f"Multi-image recipe: {recipe_draft.get('title')}")
        logger.info(f"  Ingredients: {len(recipe_draft.get('ingredients', []))}")
        logger.info(f"  Steps: {len(recipe_draft.get('steps', []))}")


if __name__ == "__main__":
    # Allow running directly for debugging
    pytest.main([__file__, "-v", "-s", "--run-integration"])
