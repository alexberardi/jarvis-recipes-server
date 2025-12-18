import asyncio

import pytest

from jarvis_recipes.app.schemas.ingestion import RecipeDraft
from jarvis_recipes.app.services import image_ingest_worker


class DummySettings:
    vision_subprocess_enabled = False
    vision_subprocess_max_retries = 0
    vision_timeout_seconds = 5
    llm_vision_model_name = "vision"


@pytest.mark.asyncio
async def test_sequential_vision_success(monkeypatch):
    calls = []

    async def fake_call(image, model_name, current_draft, image_index, image_count, is_final_image, title_hint, timeout_seconds):
        calls.append(image_index)
        return (
            RecipeDraft(
                title=f"T{image_index}",
                description=None,
                ingredients=[
                    {"name": f"ing{image_index}", "quantity": None, "unit": None, "notes": None}
                    for _ in range(3)
                ],
                steps=[f"s{image_index}", f"s{image_index}b"],
                prep_time_minutes=0,
                cook_time_minutes=0,
                total_time_minutes=0,
                servings=None,
                tags=[],
                source={"type": "image"},
            ),
            [],
        )

    monkeypatch.setattr("jarvis_recipes.app.services.llm_client.call_vision_single", fake_call)
    imgs = [b"a", b"b"]
    draft, attempts = await image_ingest_worker._run_sequential_vision(imgs, title_hint="X", settings=DummySettings())
    assert draft.title == "T2"
    assert len(attempts) == 2
    assert calls == [1, 2]


@pytest.mark.asyncio
async def test_sequential_vision_failure(monkeypatch):
    async def fake_call(*args, **kwargs):
        raise ValueError("bad json")

    monkeypatch.setattr("jarvis_recipes.app.services.llm_client.call_vision_single", fake_call)
    with pytest.raises(ValueError):
        await image_ingest_worker._run_sequential_vision([b"a"], title_hint=None, settings=DummySettings())

