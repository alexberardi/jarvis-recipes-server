from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, model_validator


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
    # Where recipe_id points. "user" is a committed recipe; "stage" is a planner
    # pick that has not been saved to the box yet and gets materialised into a
    # real recipe at commit time. Defaults to "user" so existing callers are
    # unaffected.
    source: Literal["user", "stage"] = "user"


class MealPlanItemRead(MealPlanItemBase):
    id: int
    # Denormalised from the joined recipe so a client can render a plan without a
    # request per slot. A plan of 21 meals was 21 round trips otherwise, and the
    # titles are what the screen is actually made of.
    title: Optional[str] = None
    image_url: Optional[str] = None
    total_time_minutes: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def _lift_recipe_fields(cls, value):
        """Copy the joined recipe's display fields up onto the item.

        `from_attributes` only reaches one level, so without this the three fields
        above are silently None for every ORM-sourced item -- the exact failure
        that looks like a backend bug and is a serialization one. A dict input
        (tests, hand-built payloads) passes through untouched.
        """
        recipe = getattr(value, "recipe", None)
        if recipe is None:
            return value
        return {
            "id": value.id,
            "date": value.date,
            "meal_type": value.meal_type,
            "recipe_id": value.recipe_id,
            "title": getattr(recipe, "title", None),
            "image_url": getattr(recipe, "image_url", None),
            "total_time_minutes": getattr(recipe, "total_time_minutes", None),
        }


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


class MealPlanSummary(BaseModel):
    """A plan as it appears in a list: enough to choose one, not to cook from.

    `end_date` is derived from the items rather than stored -- `start_date` is on
    the row but a plan's span is whatever days it actually covers, and a plan
    committed for Mon/Tue/Fri is three days long, not five.
    """

    id: int
    name: Optional[str] = None
    start_date: date
    end_date: date
    meal_count: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

