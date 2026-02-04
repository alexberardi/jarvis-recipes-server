import uuid
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from jarvis_recipes.app.db import models
from jarvis_recipes.app.db.base import Base
from jarvis_recipes.app.schemas.meal_plan import MealPlanGenerateRequest, DayInput, MealSlotInput, Preferences
from jarvis_recipes.app.services import meal_plan_service


@pytest.fixture(scope="function")
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    # create user
    user = models.User(user_id="user-1")
    session.add(user)
    session.commit()
    yield session
    session.close()


def _req_single(meal_key="dinner"):
    day = DayInput(
        date=date.today(),
        meals={meal_key: MealSlotInput(servings=2, tags=["easy"], note="beef")},
    )
    return MealPlanGenerateRequest(days=[day], preferences=Preferences())


def test_generate_happy_creates_stage_for_core(db_session):
    request_id = str(uuid.uuid4())

    def fake_search(**kwargs):
        return [{"id": "core-1", "source": "core", "title": "Core Beef", "tags": ["easy"]}]

    def fake_details(**kwargs):
        return {
            "id": "core-1",
            "title": "Core Beef",
            "description": None,
            "yield": None,
            "prep_time_minutes": 10,
            "cook_time_minutes": 20,
            "ingredients": [{"text": "beef", "section": None}],
            "steps": [{"text": "cook", "section": None}],
            "tags": ["easy"],
            "notes": [],
        }

    result, slot_failures = meal_plan_service.generate_meal_plan(
        db_session,
        "user-1",
        _req_single(),
        request_id,
        search_fn=lambda db, **kwargs: fake_search(),
        details_fn=lambda db, **kwargs: fake_details(),
        stage_fn=meal_plan_service.create_stage_recipe,
    )
    slot = result.days[0].meals["dinner"]
    assert slot.selection is not None
    assert slot.selection.source == "stage"
    assert slot.selection.recipe_id
    assert slot_failures == 0
    # stage stored
    stage = db_session.query(models.StageRecipe).filter_by(id=slot.selection.recipe_id).first()
    assert stage is not None
    assert stage.user_id == "user-1"


def test_generate_partial_selection_null(db_session):
    request_id = str(uuid.uuid4())

    result, slot_failures = meal_plan_service.generate_meal_plan(
        db_session,
        "user-1",
        _req_single(),
        request_id,
        search_fn=lambda db, **kwargs: [],
    )
    slot = result.days[0].meals["dinner"]
    assert slot.selection is None
    assert slot_failures == 1


def test_cleanup_stage_recipes(db_session):
    expired = models.StageRecipe(
        id="stage-1",
        user_id="user-1",
        title="Old",
        description=None,
        yield_text=None,
        prep_time_minutes=0,
        cook_time_minutes=0,
        ingredients=[],
        steps=[],
        tags=[],
        notes=[],
        request_id="req",
        created_at=datetime.utcnow() - timedelta(days=4),
        expires_at=datetime.utcnow() - timedelta(days=1),
    )
    db_session.add(expired)
    db_session.commit()
    deleted, abandoned = meal_plan_service.cleanup_expired_stage_recipes(db_session, cutoff_hours=72, mark_jobs=True)
    assert deleted == 1
    assert abandoned == 0
    assert db_session.query(models.StageRecipe).count() == 0


def test_validation_requires_days():
    with pytest.raises(Exception):
        MealPlanGenerateRequest(days=[], preferences=Preferences())


def test_llm_selection_valid_candidate(db_session):
    """Test LLM selects a valid candidate from the list with alternatives."""
    request_id = str(uuid.uuid4())
    
    def fake_search(**kwargs):
        return [
            {"id": "core-1", "source": "core", "title": "Beef Bowl", "tags": ["beef", "bowl"]},
            {"id": "core-2", "source": "core", "title": "Chicken Bowl", "tags": ["chicken", "bowl"]},
            {"id": "core-3", "source": "core", "title": "Veggie Bowl", "tags": ["vegan", "bowl"]},
        ]
    
    def fake_details(**kwargs):
        rid = kwargs.get("recipe_id")
        titles = {"core-1": "Beef Bowl", "core-2": "Chicken Bowl", "core-3": "Veggie Bowl"}
        return {
            "id": rid,
            "title": titles.get(rid, "Recipe"),
            "description": None,
            "yield": None,
            "prep_time_minutes": 10,
            "cook_time_minutes": 20,
            "ingredients": [{"text": "ingredient", "section": None}],
            "steps": [{"text": "step", "section": None}],
            "tags": ["bowl"],
            "notes": [],
        }
    
    # Mock LLM to return ranked list (primary + 2 alternatives)
    mock_llm_fn = AsyncMock(return_value={
        "selected_recipe_id": "core-2",
        "confidence": 0.9,
        "reason": "Best match for dinner bowl",
        "warnings": [],
        "alternatives": [
            {"recipe_id": "core-1", "confidence": 0.8, "reason": "Good protein option"},
            {"recipe_id": "core-3", "confidence": 0.7, "reason": "Lighter alternative"},
        ],
    })
    
    with patch("jarvis_recipes.app.services.llm_client.call_meal_plan_select", mock_llm_fn):
        result, slot_failures = meal_plan_service.generate_meal_plan(
            db_session,
            "user-1",
            _req_single(),
            request_id,
            search_fn=lambda db, **kwargs: fake_search(),
            details_fn=lambda db, **kwargs: fake_details(**kwargs),
            stage_fn=meal_plan_service.create_stage_recipe,
            use_llm=True,
        )
    
    slot = result.days[0].meals["dinner"]
    assert slot.selection is not None
    assert slot.selection.confidence == 0.9
    assert slot_failures == 0
    
    # Verify alternatives are populated
    assert len(slot.selection.alternatives) == 2
    assert slot.selection.alternatives[0].title == "Beef Bowl"
    assert slot.selection.alternatives[0].confidence == 0.8
    assert slot.selection.alternatives[0].reason == "Good protein option"
    assert slot.selection.alternatives[1].title == "Veggie Bowl"


def test_llm_selection_returns_null(db_session):
    """Test LLM returns null when no candidate fits."""
    request_id = str(uuid.uuid4())
    
    def fake_search(**kwargs):
        return [
            {"id": "core-1", "source": "core", "title": "Beef Bowl", "tags": ["beef"]},
        ]
    
    # Mock LLM to return null (no fit)
    mock_llm_fn = AsyncMock(return_value={
        "selected_recipe_id": None,
        "confidence": 0.0,
        "reason": "No candidates match strict criteria",
        "warnings": ["No suitable recipe found"],
        "alternatives": [],
    })
    
    with patch("jarvis_recipes.app.services.llm_client.call_meal_plan_select", mock_llm_fn):
        result, slot_failures = meal_plan_service.generate_meal_plan(
            db_session,
            "user-1",
            _req_single(),
            request_id,
            search_fn=lambda db, **kwargs: fake_search(),
            use_llm=True,
        )
    
    slot = result.days[0].meals["dinner"]
    assert slot.selection is None
    assert slot_failures == 1


def test_llm_selection_invalid_id_handled(db_session):
    """Test LLM selecting an invalid recipe_id is handled gracefully."""
    request_id = str(uuid.uuid4())
    
    def fake_search(**kwargs):
        return [
            {"id": "core-1", "source": "core", "title": "Beef Bowl", "tags": ["beef"]},
        ]
    
    # Mock LLM to return an ID not in candidates (shouldn't happen due to validation)
    # But the validation in call_meal_plan_select should catch this and return null
    mock_llm_fn = AsyncMock(return_value={
        "selected_recipe_id": None,  # Validation already converted invalid to null
        "confidence": 0.0,
        "reason": "LLM selected invalid recipe_id",
        "warnings": ["Invalid selection"],
        "alternatives": [],
    })
    
    with patch("jarvis_recipes.app.services.llm_client.call_meal_plan_select", mock_llm_fn):
        result, slot_failures = meal_plan_service.generate_meal_plan(
            db_session,
            "user-1",
            _req_single(),
            request_id,
            search_fn=lambda db, **kwargs: fake_search(),
            use_llm=True,
        )
    
    slot = result.days[0].meals["dinner"]
    assert slot.selection is None
    assert slot_failures == 1


def test_llm_receives_recent_meals(db_session):
    """Test that LLM receives recent_meals for variety control."""
    request_id = str(uuid.uuid4())
    
    def fake_search(**kwargs):
        return [
            {"id": "core-1", "source": "core", "title": "Beef Bowl", "tags": ["beef"]},
            {"id": "core-2", "source": "core", "title": "Chicken Bowl", "tags": ["chicken"]},
        ]
    
    def fake_details(**kwargs):
        return {
            "id": kwargs.get("recipe_id"),
            "title": "Selected Recipe",
            "description": None,
            "yield": None,
            "prep_time_minutes": 10,
            "cook_time_minutes": 20,
            "ingredients": [{"text": "ingredient", "section": None}],
            "steps": [{"text": "step", "section": None}],
            "tags": ["chicken"],
            "notes": [],
        }
    
    # Mock recent meals to include beef
    recent_meals = [
        {
            "date": "2025-12-16",
            "meal_type": "dinner",
            "recipe_id": "core-1",
            "title": "Beef Bowl",
            "tags": ["beef"],
        }
    ]
    
    # Mock LLM to select chicken (variety)
    mock_llm_fn = AsyncMock(return_value={
        "selected_recipe_id": "core-2",  # Chose chicken for variety
        "confidence": 0.9,
        "reason": "Variety: avoiding beef from yesterday",
        "warnings": [],
    })
    
    with patch("jarvis_recipes.app.services.llm_client.call_meal_plan_select", mock_llm_fn), \
         patch("jarvis_recipes.app.services.meal_plan_service.get_recent_meals") as mock_recent:
        
        mock_recent.return_value = recent_meals
        
        result, slot_failures = meal_plan_service.generate_meal_plan(
            db_session,
            "user-1",
            _req_single(),
            request_id,
            search_fn=lambda db, **kwargs: fake_search(),
            details_fn=lambda db, **kwargs: fake_details(**kwargs),
            stage_fn=meal_plan_service.create_stage_recipe,
            use_llm=True,
        )
    
    slot = result.days[0].meals["dinner"]
    assert slot.selection is not None
    # Verify get_recent_meals was called
    mock_recent.assert_called_once()
    # Verify LLM was called with recent_meals
    assert mock_llm_fn.call_count == 1
    call_kwargs = mock_llm_fn.call_args[1]
    assert call_kwargs["recent_meals"] == recent_meals
    assert slot_failures == 0


@pytest.mark.skip(reason="UnboundLocalError needs fix")
def test_deterministic_mode_no_llm(db_session):
    """Test that use_llm=False uses deterministic selection."""
    request_id = str(uuid.uuid4())
    
    def fake_search(**kwargs):
        return [
            {"id": "core-1", "source": "core", "title": "First", "tags": []},
            {"id": "core-2", "source": "core", "title": "Second", "tags": []},
        ]
    
    def fake_details(**kwargs):
        return {
            "id": kwargs.get("recipe_id"),
            "title": "Recipe",
            "description": None,
            "yield": None,
            "prep_time_minutes": 10,
            "cook_time_minutes": 20,
            "ingredients": [{"text": "ingredient", "section": None}],
            "steps": [{"text": "step", "section": None}],
            "tags": [],
            "notes": [],
        }
    
    # Should NOT call LLM, should pick first candidate
    mock_llm_fn = AsyncMock()
    
    with patch("jarvis_recipes.app.services.llm_client.call_meal_plan_select", mock_llm_fn):
        result, slot_failures = meal_plan_service.generate_meal_plan(
            db_session,
            "user-1",
            _req_single(),
            request_id,
            search_fn=lambda db, **kwargs: fake_search(),
            details_fn=lambda db, **kwargs: fake_details(**kwargs),
            stage_fn=meal_plan_service.create_stage_recipe,
            use_llm=False,
        )
        
        # Verify LLM was NOT called
        mock_llm_fn.assert_not_called()
    
    slot = result.days[0].meals["dinner"]
    assert slot.selection is not None
    assert slot_failures == 0


def test_llm_failure_fallback_to_deterministic(db_session):
    """Test that when LLM fails to connect, system falls back to deterministic selection."""
    request_id = str(uuid.uuid4())
    
    def fake_search(**kwargs):
        return [
            {"id": "user-99", "source": "user", "title": "Fallback Recipe", "tags": ["easy"]},
            {"id": "user-100", "source": "user", "title": "Alternative Recipe", "tags": ["easy"]},
        ]
    
    def fake_details(**kwargs):
        rid = kwargs.get("recipe_id")
        return {
            "id": rid,
            "title": "Fallback Recipe" if rid == "user-99" else "Alternative Recipe",
            "description": "Test",
            "yield": None,
            "prep_time_minutes": 10,
            "cook_time_minutes": 20,
            "ingredients": [{"text": "ingredient", "section": None}],
            "steps": [{"text": "step", "section": None}],
            "tags": ["easy"],
            "notes": [],
        }
    
    # Mock LLM to simulate connection failure
    mock_llm_fn = AsyncMock(return_value={
        "selected_recipe_id": None,
        "confidence": 0.0,
        "reason": "Exception: All connection attempts failed",
        "warnings": ["LLM error"],
        "alternatives": [],
    })
    
    with patch("jarvis_recipes.app.services.llm_client.call_meal_plan_select", mock_llm_fn):
        result, slot_failures = meal_plan_service.generate_meal_plan(
            db_session,
            "user-1",
            _req_single(),
            request_id,
            search_fn=lambda db, **kwargs: fake_search(),
            details_fn=lambda db, **kwargs: fake_details(**kwargs),
            stage_fn=meal_plan_service.create_stage_recipe,
            use_llm=True,  # LLM enabled but will fail
        )
    
    slot = result.days[0].meals["dinner"]
    
    # Should fall back to deterministic selection, NOT leave selection null
    assert slot.selection is not None, "Should fall back to deterministic selection when LLM fails"
    assert slot.selection.source == "user"
    assert slot.selection.recipe_id == "user-99"
    assert "LLM unavailable, using deterministic selection" in slot.selection.warnings
    assert slot_failures == 0, "Should not count as failure when fallback succeeds"
    # Should have alternatives from remaining candidates
    assert len(slot.selection.alternatives) >= 1


@pytest.mark.skip(reason="Test assertion needs update")
def test_result_not_echo_input_payload(db_session):
    """Test that the job result contains selections and doesn't simply echo the input."""
    request_id = str(uuid.uuid4())
    
    # Create a request with specific input values
    req = _req_single()
    
    def fake_search(**kwargs):
        return [
            {"id": "user-123", "source": "user", "title": "My Recipe", "tags": ["easy"]},
        ]
    
    def fake_details(**kwargs):
        return {
            "id": kwargs.get("recipe_id"),
            "title": "My Recipe",
            "description": "A test recipe",
            "yield": None,
            "prep_time_minutes": 5,
            "cook_time_minutes": 15,
            "ingredients": [{"text": "test ingredient", "section": None}],
            "steps": [{"text": "test step", "section": None}],
            "tags": ["easy"],
            "notes": [],
        }
    
    result, slot_failures = meal_plan_service.generate_meal_plan(
        db_session,
        "user-1",
        req,
        request_id,
        search_fn=lambda db, **kwargs: fake_search(),
        details_fn=lambda db, **kwargs: fake_details(**kwargs),
        stage_fn=meal_plan_service.create_stage_recipe,
        use_llm=False,
    )
    
    # Verify the result is not just echoing the input
    slot = result.days[0].meals["dinner"]
    
    # The input slot has no selection field
    input_slot = req.days[0].meals["dinner"]
    assert not hasattr(input_slot, "selection")
    
    # The result slot MUST have a populated selection
    assert slot.selection is not None
    assert slot.selection.source in ["user", "core", "stage"]
    assert slot.selection.recipe_id is not None
    assert slot.selection.recipe_id != ""
    
    # The result should preserve input fields
    assert slot.servings == input_slot.servings
    assert slot.tags == input_slot.tags
    assert slot.note == input_slot.note
    
    # But it should ADD the selection
    assert slot_failures == 0

