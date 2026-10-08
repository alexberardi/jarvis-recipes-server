from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator


def _number_as_text(value: Any) -> Any:
    """LLMs answer `4` where the schema says text; accept it as "4"."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    return value


class RecipeDraftIngredient(BaseModel):
    name: str
    quantity: Optional[str] = None
    unit: Optional[str] = None
    notes: Optional[str] = None

    _quantity_text = field_validator("quantity", mode="before")(_number_as_text)


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

    _servings_text = field_validator("servings", mode="before")(_number_as_text)

    def validate_minimums(self) -> None:
        if not self.title or len(self.title) < 3:
            raise ValueError("title too short")
        if len(self.ingredients) < 3:
            raise ValueError("not enough ingredients")
        if len(self.steps) < 2:
            raise ValueError("not enough steps")


