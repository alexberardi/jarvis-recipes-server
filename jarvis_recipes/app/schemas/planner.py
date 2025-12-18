from datetime import date
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class PlannerDraftRequest(BaseModel):
    start_date: date
    end_date: date
    preferences: Optional[str] = None


class PlannerDraftItem(BaseModel):
    date: date
    meal_type: str
    title: str


class PlannerDraftResponse(BaseModel):
    items: List[PlannerDraftItem]


class MealPlanItemBase(BaseModel):
    date: date
    meal_type: str
    recipe_id: int


class MealPlanItemCreate(MealPlanItemBase):
    pass


class MealPlanItemRead(MealPlanItemBase):
    id: int

    model_config = ConfigDict(from_attributes=True)


class MealPlanCreate(BaseModel):
    name: Optional[str] = None
    start_date: date
    items: List[MealPlanItemCreate]


class MealPlanRead(BaseModel):
    id: int
    user_id: str
    name: Optional[str] = None
    start_date: date
    items: List[MealPlanItemRead]

    model_config = ConfigDict(from_attributes=True)

