"""Joining OCR readings from several hosts.

One image goes to every OCR host because the engines fail differently and the
LLM reconciles them better than any one reads: Apple Vision gave
"2 Has butter melted", rapidocr gave "Hhsbuuenmelten", and together the model
resolved "2 tablespoons butter, melted" that neither produced alone.

The cost of asking several hosts is that one of them can be asleep. These pin the
rule that makes that survivable: continue when everyone asked has answered, or
when the deadline passes with at least one answer -- and continue exactly once
either way.
"""
from datetime import datetime

import pytest

from jarvis_recipes.app.db import models
from jarvis_recipes.app.services import ocr_join


def reading(provider: str, *texts: str, confidence: float | None = 0.9) -> list[dict]:
    """One host's per-image results, as an ocr.completed payload carries them."""
    return [
        {
            "index": i,
            "ocr_text": text,
            "truncated": False,
            "meta": {"tier": provider, "confidence": confidence, "text_len": len(text)},
            "error": None,
        }
        for i, text in enumerate(texts)
    ]


@pytest.fixture
def ingestion(db_session):
    row = models.RecipeIngestion(
        id="ing-1",
        user_id="1",
        image_s3_keys=["recipe-images/1/ing-1/0.jpg"],
        status="PENDING",
        ocr_expected=2,
    )
    db_session.add(models.User(user_id="1"))
    db_session.add(row)
    db_session.commit()
    return row


# ── collecting ────────────────────────────────────────────────────────────────


def test_a_reading_is_recorded_against_its_provider(db_session, ingestion):
    ocr_join.record_reading(db_session, ingestion, reading("apple_vision", "Crepe"))

    [stored] = ingestion.ocr_readings
    assert stored["provider"] == "apple_vision"
    assert stored["results"][0]["ocr_text"] == "Crepe"


def test_two_hosts_produce_two_readings(db_session, ingestion):
    ocr_join.record_reading(db_session, ingestion, reading("apple_vision", "Crepe"))
    count = ocr_join.record_reading(db_session, ingestion, reading("rapidocr", "drepe"))

    assert count == 2
    assert ocr_join.is_complete(ingestion)


def test_a_host_answering_twice_does_not_release_the_join_early(db_session, ingestion):
    # A redelivery or a restarted worker. Counting it twice would make the join
    # think both hosts replied and continue without the second engine.
    ocr_join.record_reading(db_session, ingestion, reading("apple_vision", "first"))
    count = ocr_join.record_reading(db_session, ingestion, reading("apple_vision", "second"))

    assert count == 1
    assert ocr_join.is_complete(ingestion) is False
    # The later answer wins; it is the more recent view of the same image.
    assert ingestion.ocr_readings[0]["results"][0]["ocr_text"] == "second"


def test_one_host_is_not_complete_when_two_were_asked(db_session, ingestion):
    ocr_join.record_reading(db_session, ingestion, reading("apple_vision", "Crepe"))

    assert ocr_join.is_complete(ingestion) is False


def test_a_single_host_install_is_complete_immediately(db_session):
    # The default configuration: one queue, one reading, nothing to wait for.
    row = models.RecipeIngestion(
        id="ing-solo", user_id="1", image_s3_keys=["k"], status="PENDING", ocr_expected=1
    )
    db_session.add(models.User(user_id="1"))
    db_session.add(row)
    db_session.commit()

    ocr_join.record_reading(db_session, row, reading("tesseract", "text"))

    assert ocr_join.is_complete(row)


def test_an_unset_expected_count_is_treated_as_one(db_session):
    # Ingestions created before this feature have ocr_expected NULL. They must
    # continue on the first reading, not wait forever for a second.
    row = models.RecipeIngestion(id="ing-old", user_id="1", image_s3_keys=["k"], status="PENDING")
    db_session.add(models.User(user_id="1"))
    db_session.add(row)
    db_session.commit()

    ocr_join.record_reading(db_session, row, reading("tesseract", "text"))

    assert ocr_join.is_complete(row)


# ── the claim ─────────────────────────────────────────────────────────────────


def test_only_one_caller_may_continue_the_pipeline(db_session, ingestion):
    # The last reading and the deadline timer race. Both continuing would
    # structure the recipe twice and bill the LLM twice for it.
    assert ocr_join.claim(db_session, ingestion.id) is True
    assert ocr_join.claim(db_session, ingestion.id) is False


def test_claiming_records_when_it_happened(db_session, ingestion):
    ocr_join.claim(db_session, ingestion.id)
    db_session.refresh(ingestion)

    assert isinstance(ingestion.ocr_joined_at, datetime)


# ── what the LLM is given ─────────────────────────────────────────────────────


def test_the_best_engine_is_listed_first_whoever_answered_first(db_session, ingestion):
    """Order is the whole mechanism that keeps the weaker engine's mistakes out.

    The prompt takes the ingredient LIST from reading 1 and uses the rest only to
    settle ambiguity. Measured on a handwritten card, treating the readings as
    peers had the model promote the stationery's printed herb caption -- which
    rapidocr reads perfectly and Apple Vision garbles -- to ingredients in 12 of
    12 runs. Ordering by arrival would hand that role to whichever host is
    fastest, which is a fact about the network, not about the reading.
    """
    ocr_join.record_reading(db_session, ingestion, reading("rapidocr", "arrived first"))
    ocr_join.record_reading(db_session, ingestion, reading("apple_vision", "arrived second"))

    assert [r["provider"] for r in ocr_join.readings_for_llm(ingestion)] == [
        "apple_vision",
        "rapidocr",
    ]


def test_an_unrecognised_engine_does_not_outrank_a_known_one(db_session, ingestion):
    # A provider added later must not silently become the authoritative reading.
    ocr_join.record_reading(db_session, ingestion, reading("something_new", "?"))
    ocr_join.record_reading(db_session, ingestion, reading("apple_vision", "good"))

    assert ocr_join.readings_for_llm(ingestion)[0]["provider"] == "apple_vision"


def test_multiple_images_are_combined_in_page_order(db_session, ingestion):
    # Recipes span two photos often enough; joining them out of order rebuilds a
    # scrambled recipe rather than failing visibly.
    results = reading("apple_vision", "page one", "page two")
    results.reverse()

    assert ocr_join.combine(results) == "page one\n\npage two"


def test_an_image_that_failed_contributes_nothing(db_session, ingestion):
    results = reading("apple_vision", "page one", "")

    assert ocr_join.combine(results) == "page one"


def test_describe_names_the_engines_that_answered(db_session, ingestion):
    ocr_join.record_reading(db_session, ingestion, reading("apple_vision", "a"))

    assert ocr_join.describe(ingestion) == "apple_vision (1/2)"
