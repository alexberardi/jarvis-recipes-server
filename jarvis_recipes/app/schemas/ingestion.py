from typing import List, Optional

from pydantic import BaseModel, Field, ValidationError


class RecipeDraftIngredient(BaseModel):
    name: str
    quantity: Optional[str] = None
    unit: Optional[str] = None
    notes: Optional[str] = None


class RecipeDraftSource(BaseModel):
    type: str = "image"
    original_filename: Optional[str] = None
    ocr_tier_used: Optional[int] = None


class RecipeDraft(BaseModel):
    title: str
    description: Optional[str] = None
    ingredients: List[RecipeDraftIngredient]
    steps: List[str]
    prep_time_minutes: int = 0
    cook_time_minutes: int = 0
    total_time_minutes: int = 0
    servings: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    source: RecipeDraftSource

    def validate_minimums(self) -> None:
        if not self.title or len(self.title) < 3:
            raise ValueError("title too short")
        if len(self.ingredients) < 3:
            raise ValueError("not enough ingredients")
        if len(self.steps) < 2:
            raise ValueError("not enough steps")


