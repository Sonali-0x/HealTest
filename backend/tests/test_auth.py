import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from app import auth


_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeSigningKey:
    key = _private_key.public_key()


class FakeJwksClient:
    def __init__(self, _uri):
        pass

    def get_signing_key_from_jwt(self, _token):
        return FakeSigningKey()


def _claims(**overrides):
    claims = {
        "iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_test",
        "sub": "tenant-user",
        "client_id": "test-client",
        "token_use": "access",
        "iat": int(time.time()),
        "exp": int(time.time()) + 600,
    }
    claims.update(overrides)
    return claims


@pytest.fixture(autouse=True)
def cognito_settings(monkeypatch):
    monkeypatch.setenv("COGNITO_REGION", "us-east-1")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_test")
    monkeypatch.setenv("COGNITO_APP_CLIENT_ID", "test-client")
    monkeypatch.setattr(auth, "_jwks_client", FakeJwksClient)


def test_current_principal_accepts_cognito_access_token():
    token = jwt.encode(_claims(), _private_key, algorithm="RS256")

    assert auth.current_principal(f"Bearer {token}") == "tenant-user"


@pytest.mark.parametrize(
    "claim_overrides",
    [
        {"iss": "https://attacker.example/pool"},
        {"sub": ""},
        {"token_use": "id"},
        {"client_id": "another-client"},
        {"exp": int(time.time()) - 10},
    ],
)
def test_current_principal_rejects_invalid_claims(claim_overrides):
    token = jwt.encode(_claims(**claim_overrides), _private_key, algorithm="RS256")

    with pytest.raises(HTTPException) as exc_info:
        auth.current_principal(f"Bearer {token}")

    assert exc_info.value.status_code == 401


@pytest.mark.parametrize("authorization", [None, "", "Basic token", "Bearer "])
def test_current_principal_rejects_missing_or_malformed_header(authorization):
    with pytest.raises(HTTPException) as exc_info:
        auth.current_principal(authorization)

    assert exc_info.value.status_code == 401


def test_validate_settings_requires_complete_cognito_configuration(monkeypatch):
    monkeypatch.delenv("COGNITO_APP_CLIENT_ID")

    with pytest.raises(RuntimeError, match="COGNITO_APP_CLIENT_ID"):
        auth.validate_settings()
