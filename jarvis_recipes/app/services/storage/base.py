from abc import ABC, abstractmethod

from fastapi import UploadFile


class StorageProvider(ABC):
    @abstractmethod
    def save_image(self, file: UploadFile) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    @abstractmethod
    def delete_image(self, url: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError

