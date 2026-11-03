"""Cognito access-token validation and tenant identity extraction."""

from __future__ import annotations

import hashlib
import os
import hmac
from functools import lru_cache

import jwt
from fastapi import Header, HTTPException
from jwt import InvalidTokenError, PyJWKClient, PyJWKClientError


def _settings() -> tuple[str, str, str]:
    region = os.environ.get("COGNITO_REGION", "").strip()
    pool_id = os.environ.get("COGNITO_USER_POOL_ID", "").strip()
    client_id = os.environ.get("COGNITO_APP_CLIENT_ID", "").strip()
    if not region or not pool_id or not client_id:
        raise RuntimeError(
            "COGNITO_REGION, COGNITO_USER_POOL_ID, and "
            "COGNITO_APP_CLIENT_ID must be configured"
        )
    return (
        f"https://cognito-idp.{region}.amazonaws.com/{pool_id}",
        client_id,
        f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/jwks.json",
    )


@lru_cache(maxsize=8)
def _jwks_client(uri: str) -> PyJWKClient:
    return PyJWKClient(uri, cache_jwk_set=True, lifespan=300, timeout=5)


def validate_settings() -> None:
    mode = os.environ.get("HEALTEST_AUTH_MODE", "cognito").strip().lower()
    if mode == "cognito":
        _settings()
        return
    if mode == "local-token":
        if os.environ.get("HEALTEST_ENV", "").strip().lower() == "production":
            raise RuntimeError("local-token authentication is forbidden in production")
        if not os.environ.get("HEALTEST_API_TOKEN", "").strip():
            raise RuntimeError("HEALTEST_API_TOKEN must be configured for local-token mode")
        return
    raise RuntimeError("HEALTEST_AUTH_MODE must be 'cognito' or 'local-token'")


def current_principal(authorization: str | None = Header(default=None)) -> str:
    mode = os.environ.get("HEALTEST_AUTH_MODE", "cognito").strip().lower()
    if mode == "local-token":
        if os.environ.get("HEALTEST_ENV", "").strip().lower() == "production":
            raise RuntimeError("local-token authentication is forbidden in production")
        token = os.environ.get("HEALTEST_API_TOKEN", "").strip()
        if not token:
            raise RuntimeError("HEALTEST_API_TOKEN must be configured for local-token mode")
        if authorization is None or not hmac.compare_digest(
            authorization, f"Bearer {token}"
        ):
            raise HTTPException(status_code=401, detail="unauthorized")
        return f"local:{hashlib.sha256(token.encode()).hexdigest()}"

    issuer, client_id, jwks_uri = _settings()
    if not authorization:
        raise HTTPException(status_code=401, detail="unauthorized")
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not token.strip():
        raise HTTPException(status_code=401, detail="unauthorized")

    try:
        signing_key = _jwks_client(jwks_uri).get_signing_key_from_jwt(token.strip())
        claims = jwt.decode(
            token.strip(),
            signing_key.key,
            algorithms=["RS256"],
            issuer=issuer,
            options={
                "verify_aud": False,
                "require": ["exp", "iat", "iss", "sub", "token_use", "client_id"],
            },
        )
    except PyJWKClientError as exc:
        raise HTTPException(status_code=503, detail="identity provider unavailable") from exc
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="unauthorized") from exc

    if claims.get("token_use") != "access" or claims.get("client_id") != client_id:
        raise HTTPException(status_code=401, detail="unauthorized")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise HTTPException(status_code=401, detail="unauthorized")
    return subject
