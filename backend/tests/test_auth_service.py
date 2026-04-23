"""Unit tests for the Supabase JWT decode helper.

These sign tokens with the project's real SUPABASE_JWT_SECRET so the decode
path uses the same configuration production will. No network, no Supabase
required.
"""

import jwt
import pytest
from fastapi import HTTPException

from app.auth.service import decode_supabase_jwt
from app.config import settings


def _make_token(payload: dict, secret: str | None = None) -> str:
    return jwt.encode(payload, secret or settings.SUPABASE_JWT_SECRET, algorithm="HS256")


def test_decode_valid_token():
    payload = {"sub": "user-123", "aud": "authenticated", "exp": 9999999999}
    token = _make_token(payload)
    result = decode_supabase_jwt(token)
    assert result["sub"] == "user-123"
    assert result["aud"] == "authenticated"


def test_reject_expired_token():
    token = _make_token({"sub": "u", "aud": "authenticated", "exp": 1})
    with pytest.raises(HTTPException) as exc:
        decode_supabase_jwt(token)
    assert exc.value.status_code == 401
    assert "expired" in exc.value.detail.lower()


def test_reject_bad_signature():
    token = _make_token(
        {"sub": "u", "aud": "authenticated", "exp": 9999999999}, secret="wrong-secret"
    )
    with pytest.raises(HTTPException) as exc:
        decode_supabase_jwt(token)
    assert exc.value.status_code == 401


def test_reject_wrong_audience():
    token = _make_token({"sub": "u", "aud": "anon", "exp": 9999999999})
    with pytest.raises(HTTPException) as exc:
        decode_supabase_jwt(token)
    assert exc.value.status_code == 401


def test_reject_malformed_token():
    with pytest.raises(HTTPException) as exc:
        decode_supabase_jwt("not.a.jwt")
    assert exc.value.status_code == 401
