"""The fan-out/join flow through the worker.

Exercises what happens when two OCR hosts answer, when only one does, and when
the deadline fires -- driving the real handlers with the LLM and the queue
stubbed, so what is under test is the join and nothing else.
"""
from unittest.mock import patch

import pytest

from jarvis_recipes.app.db import models
from jarvis_recipes.app.schemas.ingestion import RecipeDraft
from jarvis_recipes.app.services import ocr_join, parse_job_service, queue_worker
from tests.fixtures.ocr_samples import CREPE_CARD_APPLE_VISION, CREPE_CARD_RAPIDOCR

# The real readings of the same photograph. Short invented samples do not reach
# the structuring step at all -- they fail the quality gate first, which makes
# the join look broken when it is fine.
APPLE = CREPE_CARD_APPLE_VISION
RAPID = CREPE_CARD_RAPIDOCR


def completion(provider: str, text: str) -> dict:
    return {
        "status": "success",
        "results": [
            {
                "index": 0,
                "ocr_text": text,
                "truncated": False,
                "meta": {"tier": provider, "confidence": 0.9, "text_len": len(text)},
                "error": None,
            }
        ],
    }


@pytest.fixture
def setup(db_session):
    db_session.add(models.User(user_id="1"))
    ingestion = models.RecipeIngestion(
        id="ing-1", user_id="1", image_s3_keys=["k"], status="PENDING", ocr_expected=2
    )
    job = models.RecipeParseJob(
        id="job-1",
        user_id="1",
        job_type="image",
        status="RUNNING",
        job_data={"ingestion_id": "ing-1", "tier_max": 3},
        attempts=1,
    )
    db_session.add_all([ingestion, job])
    db_session.commit()
    return ingestion, job


@pytest.fixture
def captured_readings():
    """What the structuring step was actually given."""
    seen = {}

    async def fake(text, model, readings=None):
        seen["text"] = text
        seen["readings"] = readings
        # A draft that clears validate_minimums, so these tests fail for join
        # reasons and not because the stub was too thin to be a recipe.
        return RecipeDraft(
            title="Crepe",
            ingredients=[
                {"name": "flour", "quantity": "1", "unit": "cup"},
                {"name": "sugar", "quantity": "1", "unit": "tsp"},
                {"name": "salt", "quantity": "1/4", "unit": "tsp"},
                {"name": "eggs", "quantity": "3", "unit": None},
                {"name": "milk", "quantity": "2", "unit": "cups"},
                {"name": "butter, melted", "quantity": "2", "unit": "tbsp"},
            ],
            steps=[
                "Beat eggs and milk together in a blender.",
                "Add the flour mixture until smooth, then stir in melted butter.",
                "Heat a pan, wipe with oil, and cook each crepe.",
            ],
            source={"type": "ocr"},
        )

    with patch.object(queue_worker, "call_text_structuring", side_effect=fake), patch.object(
        queue_worker, "clean_and_validate_draft", side_effect=lambda d, m: d
    ):
        yield seen


@pytest.fixture(autouse=True)
def no_real_queue():
    with patch.object(queue_worker.queue_service, "schedule_join_deadline") as sched:
        yield sched


def test_the_first_reading_alone_does_not_reach_the_llm(db_session, setup, captured_readings):
    ingestion, job = setup

    queue_worker._process_ocr_completed(db_session, job, completion("apple_vision", APPLE), None)

    assert "readings" not in captured_readings
    db_session.refresh(ingestion)
    assert ingestion.ocr_joined_at is None


def test_waiting_schedules_a_deadline(db_session, setup, captured_readings, no_real_queue):
    _, job = setup

    queue_worker._process_ocr_completed(db_session, job, completion("apple_vision", APPLE), None)

    no_real_queue.assert_called_once_with("job-1", "ing-1")


def test_both_readings_reach_the_llm_together(db_session, setup, captured_readings):
    # The point of the whole feature.
    _, job = setup

    # rapidocr answers FIRST here, to prove the ordering is by engine not arrival.
    queue_worker._process_ocr_completed(db_session, job, completion("rapidocr", RAPID), None)
    queue_worker._process_ocr_completed(db_session, job, completion("apple_vision", APPLE), None)

    providers = [p for p, _ in captured_readings["readings"]]
    # Apple Vision first regardless of arrival order: the prompt takes the
    # ingredient list from reading 1 and uses the rest only to settle ambiguity.
    assert providers == ["apple_vision", "rapidocr"]
    assert captured_readings["readings"][1][1] == RAPID


def test_the_join_actually_completes_the_job(db_session, setup, captured_readings):
    """Not just "the LLM was called" -- that the import finishes.

    The other tests here assert what the structuring step was GIVEN, which is
    populated before the code that consumes its result runs. A NameError after
    that point left them all green while every real import failed with
    "text_structuring_exception". Assert the outcome, not only the input.
    """
    ingestion, job = setup

    queue_worker._process_ocr_completed(db_session, job, completion("apple_vision", APPLE), None)
    queue_worker._process_ocr_completed(db_session, job, completion("rapidocr", RAPID), None)

    db_session.refresh(job)
    db_session.refresh(ingestion)
    assert job.error_code is None, job.error_message
    assert ingestion.status == "SUCCEEDED"


def test_the_pipeline_record_names_every_engine_that_answered(db_session, setup, captured_readings):
    # With a fan-out there is no single "the" provider, and which engines were
    # reconciled is the first thing anyone debugging a wrong import wants.
    ingestion, job = setup

    queue_worker._process_ocr_completed(db_session, job, completion("apple_vision", APPLE), None)
    queue_worker._process_ocr_completed(db_session, job, completion("rapidocr", RAPID), None)

    db_session.refresh(ingestion)
    assert ingestion.pipeline_json["providers_used"] == ["apple_vision", "rapidocr"]
    assert [a["provider"] for a in ingestion.pipeline_json["attempts"]] == [
        "apple_vision",
        "rapidocr",
    ]


def test_the_gate_judges_the_best_reading_not_the_concatenation(db_session, setup, captured_readings):
    # Concatenating lets one engine's mush drag a good reading below the line.
    _, job = setup

    queue_worker._process_ocr_completed(db_session, job, completion("apple_vision", APPLE), None)
    queue_worker._process_ocr_completed(db_session, job, completion("rapidocr", RAPID), None)

    assert captured_readings["text"] == APPLE


def test_a_sleeping_host_costs_its_reading_and_nothing_else(db_session, setup, captured_readings):
    # The fault tolerance that makes fan-out safe: the Linux box never answers,
    # the deadline fires, and the import completes on Apple Vision alone -- which
    # is exactly what the single-host pipeline did before this feature.
    _, job = setup

    queue_worker._process_ocr_completed(db_session, job, completion("apple_vision", APPLE), None)
    assert "readings" not in captured_readings

    queue_worker._process_ocr_join_deadline(db_session, job, {}, "job-1")

    assert [p for p, _ in captured_readings["readings"]] == ["apple_vision"]


def test_a_lone_unusable_reading_fails_rather_than_reaching_the_llm(
    db_session, setup, captured_readings
):
    # The other half of degrading gracefully. If the ONE host that answered is a
    # printed-text engine that produced mush, continuing would ask the model to
    # invent a recipe. The gate still applies to whatever survived the join.
    _, job = setup

    queue_worker._process_ocr_completed(db_session, job, completion("rapidocr", RAPID), None)
    queue_worker._process_ocr_join_deadline(db_session, job, {}, "job-1")

    assert captured_readings == {}
    db_session.refresh(job)
    assert job.error_code == "quality_gate_failed"


def test_the_deadline_does_nothing_once_the_readings_are_in(db_session, setup, captured_readings):
    _, job = setup
    queue_worker._process_ocr_completed(db_session, job, completion("apple_vision", APPLE), None)
    queue_worker._process_ocr_completed(db_session, job, completion("rapidocr", RAPID), None)
    captured_readings.clear()

    queue_worker._process_ocr_join_deadline(db_session, job, {}, "job-1")

    assert captured_readings == {}


def test_a_reading_arriving_after_the_deadline_is_discarded(db_session, setup, captured_readings):
    # Otherwise a slow host restructures the recipe a second time.
    _, job = setup
    queue_worker._process_ocr_completed(db_session, job, completion("rapidocr", RAPID), None)
    queue_worker._process_ocr_join_deadline(db_session, job, {}, "job-1")
    captured_readings.clear()

    queue_worker._process_ocr_completed(db_session, job, completion("apple_vision", APPLE), None)

    assert captured_readings == {}


def test_no_host_answering_leaves_the_job_for_the_reaper(db_session, setup, captured_readings):
    # Every OCR host down. Nothing to structure, and nothing this handler should
    # invent -- the job stays open and the stale-job path owns it.
    _, job = setup

    queue_worker._process_ocr_join_deadline(db_session, job, {}, "job-1")

    assert captured_readings == {}


def test_a_host_that_answers_with_empty_text_is_dropped_from_the_prompt(
    db_session, setup, captured_readings
):
    # A blank block in the prompt is noise the model has to reason past.
    _, job = setup

    queue_worker._process_ocr_completed(db_session, job, completion("apple_vision", APPLE), None)
    queue_worker._process_ocr_completed(db_session, job, completion("rapidocr", ""), None)

    assert [p for p, _ in captured_readings["readings"]] == ["apple_vision"]


def test_every_host_answering_empty_fails_the_job(db_session, setup, captured_readings):
    _, job = setup

    queue_worker._process_ocr_completed(db_session, job, completion("apple_vision", ""), None)
    queue_worker._process_ocr_completed(db_session, job, completion("rapidocr", ""), None)

    db_session.refresh(job)
    assert job.status == parse_job_service.RecipeParseJobStatus.ERROR.value
    assert job.error_code == "ocr_no_text"
