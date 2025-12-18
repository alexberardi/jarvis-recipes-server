import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from jarvis_recipes.app.services.storage.base import StorageProvider


class LocalStorageProvider(StorageProvider):
    def __init__(self, media_root: Path):
        self.media_root = media_root
        self.media_root.mkdir(parents=True, exist_ok=True)

    def save_image(self, file: UploadFile) -> str:
        extension = Path(file.filename or "upload").suffix
        filename = f"{uuid4().hex}{extension}"
        destination = self.media_root / filename
        with destination.open("wb") as buffer:
            file.file.seek(0)
            shutil.copyfileobj(file.file, buffer)
        return f"/media/{filename}"

    def delete_image(self, url: str) -> None:
        prefix = "/media/"
        if not url.startswith(prefix):
            return
        filename = url[len(prefix) :]
        path = self.media_root / filename
        if path.exists():
            path.unlink()

