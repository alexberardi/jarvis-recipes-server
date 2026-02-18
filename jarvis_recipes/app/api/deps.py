from typing import Optional

import httpx
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from jarvis_recipes.app.core import service_config
from jarvis_recipes.app.core.config import get_settings
from jarvis_recipes.app.db.session import get_db
from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.services.storage.base import StorageProvider
from jarvis_recipes.app.services.storage.local import LocalStorageProvider

security = HTTPBearer(auto_error=True)


async def verify_app_auth(
    request: Request,
    x_jarvis_app_id: Optional[str] = Header(None),
    x_jarvis_app_key: Optional[str] = Header(None),
) -> None:
    """
    Enforce app-to-app authentication by forwarding headers to jarvis-auth /internal/app-ping.
    """
    if not x_jarvis_app_id or not x_jarvis_app_key:
        raise HTTPException(status_code=401, detail="Missing app credentials")

    try:
        jarvis_auth_base = service_config.get_auth_url()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    app_ping = jarvis_auth_base.rstrip("/") + "/internal/app-ping"
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(
                app_ping,
                headers={
                    "X-Jarvis-App-Id": x_jarvis_app_id,
                    "X-Jarvis-App-Key": x_jarvis_app_key,
                },
            )
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Auth service unavailable: {exc}",
            ) from exc

    if resp.status_code != 200:
        if resp.status_code == 401:
            raise HTTPException(status_code=401, detail="Invalid app credentials")
        raise HTTPException(status_code=resp.status_code, detail="App auth failed")

    # Stash calling app in request state
    request.state.calling_app_id = x_jarvis_app_id


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

