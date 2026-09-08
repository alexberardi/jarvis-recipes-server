"""Collect OCR readings from several hosts, then release them together.

One image is sent to every OCR host. Each answers independently, so the pipeline
has to wait for a set of replies rather than react to the first one -- and it has
to give up waiting, because a host that is asleep would otherwise hold an import
open forever.

The rule: continue as soon as every host asked has answered, or as soon as the
deadline passes with at least one answer in hand. A missing host costs its
reading and nothing else; the LLM simply reconciles fewer of them.

Two callers race to that continuation -- the last reading to arrive and the
deadline timer -- so the claim below is atomic. Exactly one wins; the loser
returns None and stops.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from jarvis_recipes.app.db import models

logger = logging.getLogger(__name__)


def record_reading(
    db: Session,
    ingestion: models.RecipeIngestion,
    results: list[dict[str, Any]],
) -> int:
    """Store one host's answer. Returns how many have now been recorded.

    Keyed on the PROVIDER that produced it -- apple_vision, rapidocr -- because
    that is the reading's real identity and it is already in the completion
    payload, so nothing new has to be carried across the service boundary. A host
    that answers twice (a redelivery, a restarted worker) overwrites its own
    entry rather than counting twice and tricking the join into releasing early.
    """
    provider = _provider_of(results) or "unknown"
    readings = list(ingestion.ocr_readings or [])
    readings = [r for r in readings if r.get("provider") != provider]
    readings.append(
        {
            "provider": provider,
            "results": results,
            "received_at": datetime.utcnow().isoformat(),
        }
    )
    ingestion.ocr_readings = readings
    # JSON columns are mutated by replacement, not in place; SQLAlchemy needs the
    # whole list reassigned (done above) to notice.
    db.commit()
    db.refresh(ingestion)
    return len(readings)


def _provider_of(results: list[dict[str, Any]]) -> Optional[str]:
    for result in results or []:
        tier = (result.get("meta") or {}).get("tier")
        if tier:
            return tier
    return None


def claim(db: Session, ingestion_id: str) -> bool:
    """Take the right to continue the pipeline. True for exactly one caller.

    A conditional UPDATE rather than read-then-write: the last reading and the
    deadline timer can be running on two workers at the same moment, and a
    check-then-set would let both through and structure the recipe twice.
    """
    result = db.execute(
        update(models.RecipeIngestion)
        .where(models.RecipeIngestion.id == ingestion_id)
        .where(models.RecipeIngestion.ocr_joined_at.is_(None))
        .values(ocr_joined_at=datetime.utcnow())
    )
    db.commit()
    return result.rowcount == 1


def is_complete(ingestion: models.RecipeIngestion) -> bool:
    """Has every host that was asked answered?"""
    expected = ingestion.ocr_expected or 1
    return len(ingestion.ocr_readings or []) >= expected


# Engines in descending order of how well they read a page, mirroring
# jarvis-ocr-service's DEFAULT_TIER_ORDER. Order matters to the prompt: the
# structuring step takes the ingredient LIST from the first reading and uses the
# rest only to settle what it read ambiguously.
#
# That asymmetry is not stylistic. Measured on a handwritten card, rapidocr
# garbles the recipe but reads the decoration printed on the stationery
# perfectly -- "parsley", "sage", "rosemary" beside an illustration -- and the
# model then promoted all three to ingredients in 12 of 12 runs. Apple Vision
# mangles the same caption into "i parishes" / "rosemarys", which the model
# ignores: 7 of 8 runs clean. Treating the readings as peers imports the weaker
# engine's clearest mistakes.
ENGINE_RANK = [
    "apple_vision",
    "llm_proxy_vision",
    "llm_proxy_cloud",
    "rapidocr",
    "tesseract",
    "paddleocr",
    "easyocr",
]


def _rank(provider: Optional[str]) -> int:
    try:
        return ENGINE_RANK.index(provider or "")
    except ValueError:
        # Unknown engines sort after known ones but before nothing; a new
        # provider should not silently outrank Apple Vision.
        return len(ENGINE_RANK)


def readings_for_llm(ingestion: models.RecipeIngestion) -> list[dict[str, Any]]:
    """The recorded readings, best engine first.

    Arrival order would put whichever host is fastest first, which is a fact
    about the network rather than about the reading.
    """
    readings = list(ingestion.ocr_readings or [])
    readings.sort(key=lambda r: (_rank(r.get("provider")), r.get("received_at") or ""))
    return readings


def combine(results: list[dict[str, Any]]) -> str:
    """One host's per-image results as a single string, in image order."""
    ordered = sorted(results or [], key=lambda r: r.get("index", 0))
    texts = [r.get("ocr_text", "") for r in ordered if r.get("ocr_text")]
    return "\n\n".join(texts)


def describe(ingestion: models.RecipeIngestion) -> str:
    """For logs: 'apple_vision, rapidocr (2/2)'."""
    readings = readings_for_llm(ingestion)
    names = ", ".join(r.get("provider") or "?" for r in readings) or "none"
    return f"{names} ({len(readings)}/{ingestion.ocr_expected or 1})"
