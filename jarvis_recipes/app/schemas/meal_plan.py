from datetime import date
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
    date: date
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
    recipe_id: str
    confidence: Optional[float] = None
    matched_tags: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    alternatives: List[Alternative] = Field(default_factory=list)


class MealSlotResult(MealSlotInput):
    selection: Optional[Selection] = None


class DayResult(BaseModel):
    date: date
    meals: Dict[MealType, MealSlotResult]


class MealPlanResult(BaseModel):
    days: List[DayResult]


