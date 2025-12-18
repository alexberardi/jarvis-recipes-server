from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from jarvis_recipes.app.api.deps import get_current_user, get_db_session
from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.schemas.tag import TagCreate, TagRead
from jarvis_recipes.app.services import recipes_service

router = APIRouter(tags=["tags"])


@router.get("/tags", response_model=list[TagRead])
def list_tags(
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    return recipes_service.list_tags_for_user(db, current_user.id)


@router.post("/tags", response_model=TagRead, status_code=status.HTTP_201_CREATED)
def create_tag(
    payload: TagCreate,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    # Tags are global but access is gated by auth
    tag = recipes_service.create_tag(db, payload.name)
    return tag


@router.post("/recipes/{recipe_id}/tags/{tag_id}", response_model=TagRead)
def attach_tag(
    recipe_id: int,
    tag_id: int,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    recipe = recipes_service.attach_tag(db, current_user.id, recipe_id, tag_id)
    for tag in recipe.tags:
        if tag.id == tag_id:
            return tag
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/recipes/{recipe_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
def detach_tag(
    recipe_id: int,
    tag_id: int,
    db: Session = Depends(get_db_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    recipes_service.detach_tag(db, current_user.id, recipe_id, tag_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

