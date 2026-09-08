"""The gate between OCR output and the LLM.

Its job is to reject text an extractor could only hallucinate from, and to let
through text that is imperfect but reconstructible. Both samples here are real
output from the same photograph of a handwritten recipe card -- see
tests/fixtures/ocr_samples.py.
"""
import pytest

from jarvis_recipes.app.services.ocr_quality import score_quality
from tests.fixtures.ocr_samples import CREPE_CARD_APPLE_VISION, CREPE_CARD_RAPIDOCR


def test_a_readable_handwritten_card_gets_through():
    # 366 characters. Under the old 500-char floor this was rejected for being
    # short, though "3 eggs", "1 cup flour" and "Heat pan" are all plainly there.
    result = score_quality(CREPE_CARD_APPLE_VISION, 80.0)

    assert result["gibberish"] is False
    assert result["hard_fail"] is False


def test_printed_text_ocr_on_cursive_is_still_rejected():
    # The same card, same photo, through rapidocr. Words run together, so the
    # token count collapses -- which is the signal that actually distinguishes
    # the two, not their lengths (256 vs 366 characters).
    result = score_quality(CREPE_CARD_RAPIDOCR, 80.0)

    assert result["gibberish"] is True
    assert result["hard_fail"] is True


def test_token_count_is_what_separates_them():
    good = score_quality(CREPE_CARD_APPLE_VISION, 80.0)
    bad = score_quality(CREPE_CARD_RAPIDOCR, 80.0)

    assert good["token_count"] > 50 > bad["token_count"]
    # Lengths are close enough that a length threshold cannot tell them apart.
    assert abs(good["char_count"] - bad["char_count"]) < 150


@pytest.mark.parametrize(
    "text,reason",
    [
        ("", "nothing at all"),
        ("a\n" * 40, "letters but no words"),
        ("Recipe\nIngredients\nFlour", "a few lines of a page that failed to scan"),
    ],
)
def test_genuinely_empty_output_is_still_refused(text, reason):
    assert score_quality(text, 50.0)["hard_fail"] is True, reason


def test_a_full_printed_page_passes_comfortably():
    page = "\n".join(
        [
            "Garlic Butter Pasta with Parmesan",
            "Serves 4 people. Prep 10 minutes. Cook 15 minutes.",
            "Ingredients",
            "1 pound dried spaghetti or linguine",
            "6 tablespoons unsalted butter cut into pieces",
            "4 cloves garlic finely minced",
            "1/2 cup grated parmesan cheese plus more for serving",
            "1/4 cup chopped fresh parsley leaves",
            "1 teaspoon kosher salt plus more for the pasta water",
            "Instructions",
            "1. Bring a large pot of heavily salted water to a rolling boil.",
            "2. Add the spaghetti and cook until al dente about nine minutes.",
            "3. Melt the butter in a wide skillet over medium heat.",
            "4. Toss the drained pasta with the butter and the parmesan cheese.",
        ]
    )

    result = score_quality(page, 90.0)

    assert result["hard_fail"] is False
    assert result["pass_gate"] is True
