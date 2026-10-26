from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx
from fastapi import Depends, Header, HTTPException

from .errors import api_error
from .settings import get_settings


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str | None
    claims: dict[str, Any]


@lru_cache(maxsize=1)
def _jwks() -> dict[str, Any]:
    settings = get_settings()
    url = settings.supabase_jwks_url or (f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json" if settings.supabase_url else "")
    if not url:
        return {"keys": []}
    response = httpx.get(url, timeout=5)
    response.raise_for_status()
    return response.json()


def _decode(token: str) -> AuthUser:
    settings = get_settings()
    if not settings.supabase_url:
        raise HTTPException(503, api_error("auth_not_configured", "Supabase authentication is not configured."))
    try:
        import jwt
        header = jwt.get_unverified_header(token)
        key = next((item for item in _jwks().get("keys", []) if item.get("kid") == header.get("kid")), None)
        if not key:
            raise ValueError("Signing key not found")
        signing_key = jwt.PyJWK.from_dict(key).key
        claims = jwt.decode(token, signing_key, algorithms=["ES256", "RS256"], audience="authenticated", issuer=f"{settings.supabase_url.rstrip('/')}/auth/v1")
        return AuthUser(str(claims["sub"]), claims.get("email"), claims)
    except Exception as exc:
        raise HTTPException(401, api_error("invalid_token", "The access token is invalid or expired.")) from exc


def optional_user(authorization: str | None = Header(default=None)) -> AuthUser | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, api_error("invalid_authorization", "Use a Bearer access token."))
    return _decode(token)


def current_user(user: AuthUser | None = Depends(optional_user)) -> AuthUser:
    if user is None:
        raise HTTPException(401, api_error("authentication_required", "Sign in to use this feature."))
    return user
