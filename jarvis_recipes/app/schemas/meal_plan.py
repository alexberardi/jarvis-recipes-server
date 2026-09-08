from datetime import date as date_type
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


MealType = Literal["breakfast", "lunch", "dinner", "snack", "dessert"]


class RepeatHint(BaseModel):
    mode: Literal["same", "similar"]
    count: int = Field(gt=0)


class MealSlotInput(BaseModel):
    servings: int = Field(gt=0)
    tags: List[str] = Field(default_factory=list)
    note: Optional[str] = None
    is_meal_prep: bool = False
    repeat: Optional[RepeatHint] = None


class DayInput(BaseModel):
    date: date_type
    meals: Dict[MealType, MealSlotInput]

    @field_validator("meals")
    @classmethod
    def meals_not_empty(cls, v):
        if not v:
            raise ValueError("meals must not be empty")
        return v


class HardPrefs(BaseModel):
    allergens: List[str] = Field(default_factory=list)
    excluded_ingredients: List[str] = Field(default_factory=list)
    diet: Optional[str] = None


class SoftPrefs(BaseModel):
    tags: List[str] = Field(default_factory=list)
    cuisines: List[str] = Field(default_factory=list)
    max_prep_minutes: Optional[int] = None
    max_cook_minutes: Optional[int] = None


class Preferences(BaseModel):
    hard: HardPrefs = Field(default_factory=HardPrefs)
    soft: SoftPrefs = Field(default_factory=SoftPrefs)


class MealPlanGenerateRequest(BaseModel):
    days: List[DayInput]
    preferences: Preferences = Field(default_factory=Preferences)

    @field_validator("days")
    @classmethod
    def days_not_empty(cls, v):
        if not v:
            raise ValueError("days must not be empty")
        return v


class Alternative(BaseModel):
    source: Literal["user", "core", "stage"]
    recipe_id: str
    title: str
    confidence: float
    reason: Optional[str] = None
    matched_tags: List[str] = Field(default_factory=list)


class Selection(BaseModel):
    source: Literal["user", "core", "stage"]
    # Optional so a slot can carry a REASON without a recipe -- see the
    # "already_used" path in meal_plan_service, where every matching recipe is
    # already booked on another day. Passing None here used to raise
    # "1 validation error for Selection ... input should be a valid string" and
    # fail the whole generation, not just the slot.
    recipe_id: Optional[str] = None
    confidence: Optional[float] = None
    matched_tags: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    alternatives: List[Alternative] = Field(default_factory=list)


class MealSlotResult(MealSlotInput):
    selection: Optional[Selection] = None


class DayResult(BaseModel):
    date: date_type
    meals: Dict[MealType, MealSlotResult]


class MealPlanResult(BaseModel):
    days: List[DayResult]


# ── Random plans ──────────────────────────────────────────────────────────────
# The default planning path: instant, no job, no polling. See
# services/random_plan_service for why this is separate from the LLM planner.


class RandomSlotRequest(BaseModel):
    date: date_type
    meal_type: str


class RandomPlanRequest(BaseModel):
    slots: List[RandomSlotRequest]
    # Recipes the caller already has on screen. Sent so a re-roll of the whole
    # plan does not hand back what was just rejected.
    exclude_recipe_ids: List[int] = []


class RandomSlotResult(BaseModel):
    # Optional because a re-roll returns one recipe for a slot the client already
    # has on screen -- it knows the date; echoing it back proves nothing.
    date: Optional[date_type] = None
    meal_type: str
    recipe_id: Optional[int] = None
    title: Optional[str] = None
    image_url: Optional[str] = None
    total_time_minutes: Optional[int] = None
    servings: Optional[int] = None


class RandomPlanResponse(BaseModel):
    slots: List[RandomSlotResult]
    # True when the box ran out before every slot was filled -- the client shows
    # those as empty rather than pretending the plan is complete.
    incomplete: bool = False


class RerollRequest(BaseModel):
    meal_type: Optional[str] = None
    exclude_recipe_ids: List[int] = []
    # Advanced plans configure tags per slot; a re-roll there has to honour them
    # or it silently swaps a "quick lunch" for a 3-hour braise. Treated the same
    # way as meal_type: preferred, then relaxed rather than returning nothing.
    tags: List[str] = []
