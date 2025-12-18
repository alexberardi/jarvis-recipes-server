# Auth Integration for Jarvis Recipes

This document defines how **jarvis-recipes** integrates with **jarvis-auth** and the **Jarvis Recipes mobile app** for authentication and authorization.

The goal is to:
- Use **JWT access tokens** issued by jarvis-auth
- Verify tokens **locally** in jarvis-recipes
- Require authentication for all recipe and planner operations
- Keep responsibilities cleanly separated between services

This file is the single source of truth for auth behavior in jarvis-recipes.

---

## Overview

- Authentication is handled by **jarvis-auth**.
- Authorization and data scoping are handled by **jarvis-recipes**.
- The mobile app talks directly to both services:
  - It logs in and refreshes tokens via **jarvis-auth**.
  - It calls recipes and planner endpoints via **jarvis-recipes**, attaching the `access_token`.
- jarvis-recipes **does not** call jarvis-auth on every request. It validates JWTs locally using a shared secret key.

---

## Token Model

### Access Token

- Type: JWT
- Algorithm: `HS256`
- Issuer: `jarvis-auth`
- Consumers: `jarvis-recipes` and any other Jarvis microservices
- Typical expiry: 15–30 minutes (configured in jarvis-auth)

Required claims in the access token payload:
- `sub`: string user id (example: `"1"`)
- `email`: user email (example: `"alex@example.com"`)
- `exp`: numeric timestamp expiration (standard JWT `exp`)

Example decoded payload:

```json
{
  "sub": "1",
  "email": "alex@example.com",
  "exp": 1733372800
}
```

jarvis-recipes must not rely on any other claim for core authorization logic, but may accept additional optional claims if added later.

### Refresh Token

- Issued and stored by **jarvis-auth** only.
- Not used or stored by **jarvis-recipes**.
- Mobile app uses refresh token directly with jarvis-auth to obtain new access tokens.

---

## Environment and Secrets

Both jarvis-auth and jarvis-recipes use a **shared secret key** for HS256 JWT signing and verification.

In jarvis-recipes, configure via environment variables (names may already exist in the project and should be aligned):

```bash
AUTH_SECRET_KEY="a_long_random_secure_value"  # must match jarvis-auth
AUTH_ALGORITHM="HS256"
```

These values should be wired into a central settings/config module, for example `app/core/config.py`, and imported where needed.

jarvis-recipes must never expose the secret key in any API response or logs.

---

## Responsibility Split

### jarvis-auth

- Validates user credentials (email and password).
- Issues access tokens and refresh tokens.
- Refreshes access tokens using valid refresh tokens.
- May provide `/auth/me` or other identity endpoints.

### jarvis-recipes

- Does **not** handle login or password verification.
- Does **not** issue or refresh tokens.
- Accepts access tokens in the `Authorization` header.
- Verifies tokens locally and extracts the `sub` claim as the `user_id`.
- Uses `user_id` to scope all recipe, tag, and meal plan operations.

---

## Mobile App Flow

### Login

1. Mobile app sends credentials to **jarvis-auth** at `POST /auth/login`.
2. jarvis-auth returns:
   - `access_token`
   - `refresh_token`
   - basic user info (id, email, username)
3. Mobile app stores tokens securely (e.g. secure storage).

### Normal Requests

For all calls to protected jarvis-recipes endpoints, the mobile app must include:

```http
Authorization: Bearer <access_token>
```

jarvis-recipes will:
- Verify the token using `AUTH_SECRET_KEY` and `AUTH_ALGORITHM`.
- Extract `sub` as `user_id`.
- Reject requests with missing, invalid, or expired tokens.

### Token Refresh

1. If a request to jarvis-recipes returns 401 due to token expiry, the mobile app calls `POST /auth/refresh` on jarvis-auth with the `refresh_token`.
2. jarvis-auth returns a new `access_token`.
3. Mobile app retries the original request with the new access token.

jarvis-recipes itself does not participate in refresh logic beyond returning 401 when the token is expired or invalid.

---

## Required Behavior in jarvis-recipes

### Authorization Header

All protected endpoints in jarvis-recipes must:
- Require the `Authorization: Bearer <access_token>` header.
- Return HTTP 401 if the header is missing or malformed.

Protected endpoints include, but are not limited to:
- `/recipes` and all sub-routes
- `/tags` and all sub-routes
- `/planner` and all sub-routes

### Dependency: get_current_user

jarvis-recipes must define a FastAPI dependency to handle token parsing and verification.

Suggested location: `app/api/deps.py`.

Behavior:
- Parse the `Authorization` header using `HTTPBearer`.
- Decode the JWT using `AUTH_SECRET_KEY` and `AUTH_ALGORITHM`.
- Extract `sub` and `email` from the payload.
- Convert `sub` to an integer `user_id`.
- On any error (missing header, invalid token, expired token), raise `HTTPException` with status code 401.

Example shape (pseudocode, Cursor should implement concrete code):

```python
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from pydantic import BaseModel

security = HTTPBearer()

class CurrentUser(BaseModel):
    id: int
    email: str | None = None

def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(security),
) -> CurrentUser:
    token = creds.credentials
    # 1) decode JWT
    # 2) pull sub and email
    # 3) handle JWTError and missing claims
    # 4) return CurrentUser
    ...
```

All protected route handlers must accept a `current_user: CurrentUser = Depends(get_current_user)` parameter and use `current_user.id` for data scoping.

### Data Scoping

Every query and mutation touching recipes, tags, or meal plans must:
- Filter by `user_id = current_user.id`.
- Prevent users from accessing or modifying data owned by other users.

If a requested resource (for example, a recipe id) does not belong to the current user, jarvis-recipes should return 404 or 403. For simplicity, 404 is acceptable in the MVP.

---

## Error Handling

When token verification fails in jarvis-recipes, the API should respond with:

- Status: `401 Unauthorized`
- Body:

```json
{
  "detail": "Invalid or expired token"
}
```

For missing or malformed authorization headers, a similar 401 response is acceptable.

jarvis-recipes should not proxy auth errors from jarvis-auth, since it does not call jarvis-auth on normal requests.

---

## Testing Requirements

The following tests must exist in jarvis-recipes for auth integration:

1. Calling a protected endpoint **without** `Authorization` header returns 401.
2. Calling a protected endpoint with an **invalid** token returns 401.
3. Calling a protected endpoint with a **valid** token returns 200 and only returns data for that `user_id`.
4. If tests construct tokens manually, they must use the same `AUTH_SECRET_KEY` and `AUTH_ALGORITHM` values as the app settings.

Tests should live in a dedicated auth and/or routes test module (for example, `tests/test_auth_required.py`).

---

## Service-to-Service Communication

jarvis-recipes must not:
- Call jarvis-auth on every request to validate tokens.
- Depend on jarvis-auth availability for normal recipe operations.

Future optional patterns (not MVP):
- jarvis-recipes may call a user profile endpoint on jarvis-auth if it needs more user details than provided in the JWT.
- jarvis-recipes may support key rotation or JWKS-based public key retrieval if switching to `RS256`.

---

## Summary

- jarvis-auth is the **only** service that authenticates credentials and issues tokens.
- jarvis-recipes trust tokens signed with the shared secret and verifies them locally.
- The mobile app is responsible for attaching access tokens and refreshing them via jarvis-auth.
- All recipe and planner operations in jarvis-recipes are scoped by the `sub` claim from the JWT, treated as `user_id`.

This contract should be followed exactly when implementing authentication and authorization behavior in jarvis-recipes.
