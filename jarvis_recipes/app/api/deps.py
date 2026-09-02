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
from jarvis_recipes.app.services.settings_service import get_settings_service
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


# Key material is bound to the algorithm FAMILY, never a single shared variable.
# That is what makes the HS256/RS256 dual-accept window safe: an attacker who
# signs HS256 using the (published, readable) RSA public key as the HMAC secret
# is verified against auth_secret_key instead, and fails.
SYMMETRIC_ALGORITHMS = frozenset({"HS256"})
ASYMMETRIC_ALGORITHMS = frozenset({"RS256"})
SUPPORTED_ALGORITHMS = SYMMETRIC_ALGORITHMS | ASYMMETRIC_ALGORITHMS

_public_key_cache: str | None = None


def _rs256_public_key() -> str | None:
    """Fetch and cache jarvis-auth's public key.

    Implemented here rather than via jarvis-auth-client on purpose: this service
    is being decoupled from the Jarvis stack, so taking a new dependency on a
    Jarvis library to verify a token would move it in the wrong direction. The
    public key is fetched over plain HTTP from a URL this service already knows.

    Cached for the process lifetime: a *running* service must keep verifying if
    jarvis-auth goes down. Only a cold start during an outage fails, and it fails
    closed.
    """
    global _public_key_cache
    if _public_key_cache:
        return _public_key_cache

    auth_url = service_config.get_auth_url()
    if not auth_url:
        return None
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{auth_url.rstrip('/')}/auth/public-key")
            resp.raise_for_status()
            _public_key_cache = resp.json().get("public_key")
    except (httpx.HTTPError, ValueError):
        return None
    return _public_key_cache


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> CurrentUser:
    settings = get_settings()
    # AUTH_SECRET_KEY stays a pydantic secret. The algorithm is no longer read
    # from settings for *verification* — during the HS256 -> RS256 migration a
    # verifier must accept both, so the algorithm is taken per token from an
    # explicit allowlist instead. The settings key still governs what jarvis-auth
    # MINTS; it is not this service's business what it accepts.
    try:
        header = jwt.get_unverified_header(credentials.credentials)
        algorithm = header.get("alg")
        if algorithm not in SUPPORTED_ALGORITHMS:
            # Covers "none" and anything else exotic.
            raise JWTError(f"Unsupported token algorithm: {algorithm!r}")

        if algorithm in ASYMMETRIC_ALGORITHMS:
            key = _rs256_public_key()
            if not key:
                # Fail CLOSED — an RS256 token we cannot check is not accepted.
                raise JWTError("No RS256 public key available")
        else:
            key = settings.auth_secret_key

        payload = jwt.decode(credentials.credentials, key, algorithms=[algorithm])
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

