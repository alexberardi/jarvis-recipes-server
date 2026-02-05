"""Pydantic models for URL recipe parsing."""

from typing import List, Optional

from pydantic import BaseModel, Field


class ParsedIngredient(BaseModel):
    """A parsed ingredient with optional quantity and unit."""

    text: str
    quantity_display: Optional[str] = None
    unit: Optional[str] = None


class ParsedRecipe(BaseModel):
    """A fully parsed recipe from a URL."""

    title: str
    description: Optional[str] = None
    source_url: Optional[str] = None
    image_url: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    servings: Optional[int] = None
    estimated_time_minutes: Optional[int] = None
    ingredients: List[ParsedIngredient] = Field(default_factory=list)
    steps: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class ParseResult(BaseModel):
    """Result of a recipe parsing attempt."""

    success: bool
    recipe: Optional[ParsedRecipe] = None
    used_llm: bool = False
    parser_strategy: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    next_action: Optional[str] = None
    next_action_reason: Optional[str] = None


class PreflightResult(BaseModel):
    """Result of a URL preflight check."""

    ok: bool
    status_code: Optional[int] = None
    content_type: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    next_action: Optional[str] = None
    next_action_reason: Optional[str] = None
