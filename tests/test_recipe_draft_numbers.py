"""Models answer `"servings": 4`, not `"servings": "4"`.

Found end to end against jarvisd with Qwen3-4B: the whole photo import --
OCR, callback, queue, LLM -- worked, and then the draft was thrown away
because one field was a number. A string field that receives a number takes
it; it is the same fact.
"""
from jarvis_recipes.app.schemas.ingestion import RecipeDraft


def test_numeric_servings_and_quantities_are_accepted_as_text():
    draft = RecipeDraft.model_validate(
        {
            "title": "Pancakes",
            "ingredients": [{"name": "flour", "quantity": 2, "unit": "cups"}, {"name": "eggs", "quantity": 1.5}],
            "steps": ["Mix.", "Cook."],
            "servings": 4,
            "source": {"type": "ocr"},
        }
    )
    assert draft.servings == "4"
    assert [i.quantity for i in draft.ingredients] == ["2", "1.5"]


def test_text_and_missing_values_are_unchanged():
    draft = RecipeDraft.model_validate(
        {
            "title": "Pancakes",
            "ingredients": [{"name": "salt", "quantity": "a pinch"}, {"name": "milk"}],
            "steps": ["Mix.", "Cook."],
            "servings": "4-6",
            "source": {"type": "ocr"},
        }
    )
    assert draft.servings == "4-6"
    assert [i.quantity for i in draft.ingredients] == ["a pinch", None]
