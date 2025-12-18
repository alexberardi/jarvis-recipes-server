from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from jarvis_recipes.app.core.config import get_settings
from jarvis_recipes.app.db.session import get_db
from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.services.storage.base import StorageProvider
from jarvis_recipes.app.services.storage.local import LocalStorageProvider

security = HTTPBearer(auto_error=True)


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> CurrentUser:
    settings = get_settings()
    try:
        payload = jwt.decode(credentials.credentials, settings.auth_secret_key, algorithms=[settings.auth_algorithm])
        sub = payload.get("sub")
        if sub is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
        user_id = int(sub)
        email = payload.get("email")
        return CurrentUser(id=user_id, email=email)
    except (JWTError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")


def get_db_session(db: Session = Depends(get_db)) -> Session:
    return db


def get_storage_provider() -> StorageProvider:
    settings = get_settings()
    return LocalStorageProvider(settings.media_root)

