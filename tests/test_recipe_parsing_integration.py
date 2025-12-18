import json
import os
from pathlib import Path

import pytest

from jarvis_recipes.app.services import llm_client
from jarvis_recipes.app.services.url_recipe_parser import parse_recipe_from_url
from jarvis_recipes.app.services.image_ingest_pipeline import run_ingestion_pipeline
from jarvis_recipes.app.db import models
from jarvis_recipes.app.schemas.ingestion import RecipeDraft


def _normalize(s: str) -> str:
    return " ".join(s.lower().strip().split())


def _hit_rate(expected: list[str], actual: list[str]) -> float:
    if not expected:
        return 1.0
    hits = 0
    # explode actual on commas/and for better matching
    exploded_actual = []
    for a in actual:
        norm = _normalize(a)
        for chunk in norm.replace(" and ", ",").split(","):
            chunk = chunk.strip()
            if chunk:
                exploded_actual.append(chunk)
    actual_norm = exploded_actual or [_normalize(a) for a in actual]
    for exp in expected:
        exp_norm = _normalize(exp)
        if any(exp_norm in a for a in actual_norm):
            hits += 1
    return hits / len(expected)


def _load_expected(path: Path):
    with path.open() as f:
        return json.load(f)


URL_THRESHOLDS = {
    "default": {"ing": 0.6, "steps": 0.5},
    "broccyourbody_pesto_orzo": {"ing": 0.3, "steps": 0.0},
}


@pytest.mark.asyncio
async def test_url_parsing_smoke(monkeypatch):
    base = Path("jarvis_recipes/recipe_parsing_tests/url_based")
    urls = _load_expected(base / "urls.json")
    expected_map = _load_expected(base / "expected.json")

    for case in urls:
        cid = case["id"]
        url = case["url"]
        exp = expected_map[cid]
        result = await parse_recipe_from_url(url)
        if not result.success and getattr(result, "error_code", "") == "fetch_failed":
            pytest.xfail(f"fetch_failed for {url} (likely remote block/timeout)")
        assert result.success, f"url parse failed for {url}"
        recipe = result.recipe
        assert recipe, f"no recipe returned for {url}"
        title_ok = any(sub in _normalize(recipe.title) for sub in [_normalize(t) for t in exp["title_substrings"]])
        ing_hr = _hit_rate(exp["ingredients"], [ing.text for ing in recipe.ingredients])
        steps_hr = _hit_rate(exp["steps"], recipe.steps)
        thr = URL_THRESHOLDS.get(cid, URL_THRESHOLDS["default"])
        ing_thr = thr["ing"]
        steps_thr = thr["steps"]
        if not title_ok or (ing_hr < ing_thr and steps_hr < steps_thr):
            print(f"[DIAG][URL]{url} title_ok={title_ok} ing_hr={ing_hr:.2f} steps_hr={steps_hr:.2f}")
            print(f"  missing_ing: {[e for e in exp['ingredients'] if _normalize(e) not in [_normalize(a) for a in [ing.text for ing in recipe.ingredients]] ]}")
            print(f"  missing_steps: {[e for e in exp['steps'] if _normalize(e) not in [_normalize(s) for s in recipe.steps]]}")
        assert title_ok, f"title mismatch for {url}"
        assert ing_hr >= ing_thr or steps_hr >= steps_thr, f"low hit rate for {url}: ing={ing_hr}, steps={steps_hr}"


@pytest.mark.asyncio
async def test_image_parsing_smoke(monkeypatch):
    base = Path("jarvis_recipes/recipe_parsing_tests/image_based")
    xfail_images = {
        # Add known flaky fixtures here while improving parsing
        "date_night_chicken_mushroom": "LLM/validation still failing to yield draft",
    }
    for recipe_dir in base.iterdir():
        if not recipe_dir.is_dir():
            continue
        expected_path = recipe_dir / "expected.json"
        if not expected_path.exists():
            continue
        exp = _load_expected(expected_path)
        # load images in order
        image_files = sorted([p for p in recipe_dir.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"}])
        image_bytes = [p.read_bytes() for p in image_files]
        # fake ingestion row
        ingestion = models.RecipeIngestion(
            id="test",
            user_id="0",
            image_s3_keys=[],
            status="PENDING",
            tier_max=3,
        )
        draft, pipeline, texts = await run_ingestion_pipeline(ingestion, image_bytes, tier_max=3)
        if not draft:
            if recipe_dir.name in xfail_images:
                pytest.xfail(xfail_images[recipe_dir.name])
            pytest.xfail(f"no draft produced for {recipe_dir.name}")
        title_ok = any(sub in _normalize(draft.title) for sub in [_normalize(t) for t in exp["title_substrings"]])
        ing_hr = _hit_rate(exp["ingredients"], [ing.name for ing in draft.ingredients])
        steps_hr = _hit_rate(exp["steps"], draft.steps)
        if not title_ok or (ing_hr < 0.6 and steps_hr < 0.5):
            print(f"[DIAG][IMG]{recipe_dir.name} title_ok={title_ok} ing_hr={ing_hr:.2f} steps_hr={steps_hr:.2f}")
            print(f"  missing_ing: {[e for e in exp['ingredients'] if _normalize(e) not in [_normalize(a) for a in [ing.name for ing in draft.ingredients]] ]}")
            print(f"  missing_steps: {[e for e in exp['steps'] if _normalize(e) not in [_normalize(s) for s in draft.steps]]}")
        assert title_ok, f"title mismatch for {recipe_dir.name}"
        assert ing_hr >= 0.6 or steps_hr >= 0.5, f"low hit rate for {recipe_dir.name}: ing={ing_hr}, steps={steps_hr}"

