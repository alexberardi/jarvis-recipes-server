from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel

from jarvis_recipes.app.api.deps import get_current_user, get_storage_provider
from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.schemas.recipe import RecipeDraft
from jarvis_recipes.app.services.storage.base import StorageProvider

router = APIRouter(prefix="/recipes/import", tags=["import"])


class ImportUrlRequest(BaseModel):
    url: str


@router.post("/image", response_model=RecipeDraft)
async def import_from_image(
    file: UploadFile = File(...),
    storage: StorageProvider = Depends(get_storage_provider),
    current_user: CurrentUser = Depends(get_current_user),
):
    image_url = storage.save_image(file)
    return RecipeDraft(
        title="Draft from image",
        ingredients=["1 cup ingredient A", "2 tbsp ingredient B"],
        steps=["Step 1: placeholder", "Step 2: placeholder"],
        tags=[],
        image_url=image_url,
    )


@router.post("/url", response_model=RecipeDraft)
def import_from_url(
    payload: ImportUrlRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    return RecipeDraft(
        title="Draft from URL",
        ingredients=["1 cup ingredient A", "2 tbsp ingredient B"],
        steps=["Step 1: placeholder", "Step 2: placeholder"],
        tags=[],
    )

