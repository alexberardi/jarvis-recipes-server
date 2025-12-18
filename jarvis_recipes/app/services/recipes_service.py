from typing import Iterable, List, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from jarvis_recipes.app.db import models
from jarvis_recipes.app.schemas.recipe import IngredientCreate, RecipeCreate, RecipeUpdate
from jarvis_recipes.app.services.quantity_parser import parse_quantity_display


def _ensure_user(db: Session, user_id: str) -> models.User:
    user = db.get(models.User, user_id)
    if user is None:
        user = models.User(user_id=user_id)
        db.add(user)
        db.flush()
    return user


def _get_or_create_tag(db: Session, name: str) -> models.Tag:
    normalized = name.strip()
    if not normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tag name required")

    stmt = select(models.Tag).where(func.lower(models.Tag.name) == normalized.lower())
    tag = db.scalars(stmt).first()
    if tag:
        return tag

    tag = models.Tag(name=normalized)
    db.add(tag)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        stmt = select(models.Tag).where(func.lower(models.Tag.name) == normalized.lower())
        tag = db.scalars(stmt).first()
        if tag:
            return tag
        raise
    return tag


def _replace_steps(recipe: models.Recipe, steps: Iterable) -> None:
    recipe.steps.clear()
    for step in steps:
        recipe.steps.append(
            models.Step(step_number=step.step_number, text=step.text),
        )


def _replace_ingredients(recipe: models.Recipe, ingredients: Iterable[IngredientCreate]) -> None:
    recipe.ingredients.clear()
    for ingredient in ingredients:
        quantity_value = parse_quantity_display(ingredient.quantity_display)
        recipe.ingredients.append(
            models.Ingredient(
                text=ingredient.text,
                quantity_display=ingredient.quantity_display,
                quantity_value=quantity_value,
                unit=ingredient.unit,
            )
        )


def create_recipe(db: Session, user_id: int, data: RecipeCreate) -> models.Recipe:
    user_id_str = str(user_id)
    _ensure_user(db, user_id_str)
    total_time = data.total_time_minutes
    if total_time is None and (data.prep_time_minutes is not None or data.cook_time_minutes is not None):
        total_time = (data.prep_time_minutes or 0) + (data.cook_time_minutes or 0)

    recipe = models.Recipe(
        user_id=user_id_str,
        title=data.title,
        description=data.description,
        image_url=data.image_url,
        source_type=data.source_type,
        source_url=data.source_url,
        servings=data.servings,
        total_time_minutes=total_time,
    )
    _replace_ingredients(recipe, data.ingredients)
    _replace_steps(recipe, data.steps)
    recipe.tags = [_get_or_create_tag(db, name) for name in data.tags]

    db.add(recipe)
    db.commit()
    db.refresh(recipe)
    return recipe


def list_recipes(db: Session, user_id: str) -> List[models.Recipe]:
    user_id_str = str(user_id)
    stmt = select(models.Recipe).where(models.Recipe.user_id == user_id_str).order_by(models.Recipe.created_at.desc())
    return list(db.scalars(stmt).all())


def get_recipe(db: Session, user_id: str, recipe_id: int) -> models.Recipe:
    user_id_str = str(user_id)
    stmt = select(models.Recipe).where(models.Recipe.user_id == user_id_str, models.Recipe.id == recipe_id)
    recipe = db.scalars(stmt).first()
    if not recipe:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe not found")
    return recipe


def update_recipe(db: Session, user_id: str, recipe_id: int, data: RecipeUpdate) -> models.Recipe:
    recipe = get_recipe(db, user_id, recipe_id)

    for field in ("title", "description", "servings", "source_type", "source_url", "image_url"):
        value = getattr(data, field)
        if value is not None:
            setattr(recipe, field, value)

    total_time = data.total_time_minutes
    if total_time is None and (data.prep_time_minutes is not None or data.cook_time_minutes is not None):
        total_time = (data.prep_time_minutes or 0) + (data.cook_time_minutes or 0)
    if total_time is not None:
        recipe.total_time_minutes = total_time

    if data.ingredients is not None:
        _replace_ingredients(recipe, data.ingredients)
    if data.steps is not None:
        _replace_steps(recipe, data.steps)
    if data.tags is not None:
        recipe.tags = [_get_or_create_tag(db, name) for name in data.tags]

    db.commit()
    db.refresh(recipe)
    return recipe


def delete_recipe(db: Session, user_id: str, recipe_id: int) -> None:
    recipe = get_recipe(db, user_id, recipe_id)
    db.delete(recipe)
    db.commit()


def list_tags_for_user(db: Session, user_id: str) -> List[models.Tag]:
    user_id_str = str(user_id)
    stmt = (
        select(models.Tag)
        .join(models.recipe_tags, models.Tag.id == models.recipe_tags.c.tag_id)
        .join(models.Recipe, models.recipe_tags.c.recipe_id == models.Recipe.id)
        .where(models.Recipe.user_id == user_id_str)
        .group_by(models.Tag.id)
        .order_by(models.Tag.name.asc())
    )
    return list(db.scalars(stmt).all())


def create_tag(db: Session, name: str) -> models.Tag:
    return _get_or_create_tag(db, name)


def attach_tag(db: Session, user_id: str, recipe_id: int, tag_id: int) -> models.Recipe:
    recipe = get_recipe(db, user_id, recipe_id)
    tag = db.get(models.Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")
    if tag not in recipe.tags:
        recipe.tags.append(tag)
        db.commit()
        db.refresh(recipe)
    return recipe


def detach_tag(db: Session, user_id: str, recipe_id: int, tag_id: int) -> models.Recipe:
    recipe = get_recipe(db, user_id, recipe_id)
    tag = db.get(models.Tag, tag_id)
    if not tag or tag not in recipe.tags:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not attached")
    recipe.tags.remove(tag)
    db.commit()
    db.refresh(recipe)
    return recipe

