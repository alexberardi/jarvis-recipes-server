from datetime import datetime
from typing import List, Optional

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator

from jarvis_recipes.app.db.models import SourceType
from jarvis_recipes.app.schemas.tag import TagRead


class IngredientBase(BaseModel):
    text: str
    quantity_display: Optional[str] = None
    quantity_value: Optional[Decimal] = None
    unit: Optional[str] = None


class IngredientCreate(IngredientBase):
    pass


class IngredientRead(IngredientBase):
    id: int

    model_config = ConfigDict(from_attributes=True)


class StepBase(BaseModel):
    step_number: int
    text: str


class StepCreate(StepBase):
    pass


class StepRead(StepBase):
    id: int

    model_config = ConfigDict(from_attributes=True)


class RecipeBase(BaseModel):
    title: str
    description: Optional[str] = None
    servings: Optional[int] = None
    prep_time_minutes: Optional[int] = None
    cook_time_minutes: Optional[int] = None
    total_time_minutes: Optional[int] = None
    source_type: SourceType = SourceType.MANUAL
    source_url: Optional[str] = None
    image_url: Optional[str] = None


class RecipeCreate(RecipeBase):
    ingredients: List[IngredientCreate]
    steps: List[StepCreate]
    tags: List[str] = []
    parse_job_id: Optional[str] = None

    @field_validator("ingredients")
    @classmethod
    def validate_ingredients(cls, value: List[IngredientCreate]) -> List[IngredientCreate]:
        if not value:
            raise ValueError("At least one ingredient is required")
        return value

    @field_validator("steps")
    @classmethod
    def validate_steps(cls, value: List[StepCreate]) -> List[StepCreate]:
        if not value:
            raise ValueError("At least one step is required")
        return value


class RecipeUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    servings: Optional[int] = None
    prep_time_minutes: Optional[int] = None
    cook_time_minutes: Optional[int] = None
    total_time_minutes: Optional[int] = None
    source_type: Optional[SourceType] = None
    source_url: Optional[str] = None
    image_url: Optional[str] = None
    ingredients: Optional[List[IngredientCreate]] = None
    steps: Optional[List[StepCreate]] = None
    tags: Optional[List[str]] = None


class RecipeRead(RecipeBase):
    id: int
    user_id: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    ingredients: List[IngredientRead]
    steps: List[StepRead]
    tags: List[TagRead]

    model_config = ConfigDict(from_attributes=True)


class RecipeDraft(BaseModel):
    title: str
    ingredients: List[str]
    steps: List[str]
    tags: List[str] = []
    image_url: Optional[str] = None

