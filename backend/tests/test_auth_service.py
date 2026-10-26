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


def test_reject_unsupported_algorithm():
    """`none` / unknown algs must be rejected before we touch key material."""
    unsigned = jwt.encode(
        {"sub": "u", "aud": "authenticated", "exp": 9999999999},
        key="",
        algorithm="none",
    )
    with pytest.raises(HTTPException) as exc:
        decode_supabase_jwt(unsigned)
    assert exc.value.status_code == 401


# --- ES256 / JWKS path ---------------------------------------------------
#
# We spin up a test keypair, monkey-patch the PyJWKClient resolver to hand
# back a signing key wrapping our public key, then sign tokens with the
# matching private key. No network, no Supabase.


class _FakeSigningKey:
    def __init__(self, key):
        self.key = key


def _make_es256_keypair():
    from cryptography.hazmat.primitives.asymmetric import ec

    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key()
    return priv, pub


def _patch_jwks(monkeypatch, public_key):
    from jwt import PyJWKClient

    monkeypatch.setattr(
        PyJWKClient,
        "get_signing_key_from_jwt",
        lambda self, token: _FakeSigningKey(public_key),
    )


def test_decode_es256_via_jwks(monkeypatch):
    priv, pub = _make_es256_keypair()
    _patch_jwks(monkeypatch, pub)
    token = jwt.encode(
        {"sub": "user-es", "aud": "authenticated", "exp": 9999999999},
        priv,
        algorithm="ES256",
    )
    result = decode_supabase_jwt(token)
    assert result["sub"] == "user-es"


def test_reject_es256_wrong_signer(monkeypatch):
    _, pub = _make_es256_keypair()
    other_priv, _ = _make_es256_keypair()
    _patch_jwks(monkeypatch, pub)
    token = jwt.encode(
        {"sub": "evil", "aud": "authenticated", "exp": 9999999999},
        other_priv,  # signed with a different private key
        algorithm="ES256",
    )
    with pytest.raises(HTTPException) as exc:
        decode_supabase_jwt(token)
    assert exc.value.status_code == 401


def test_reject_alg_confusion_hs256_with_public_key_bytes(monkeypatch):
    """Classic alg-confusion: attacker mints an HS256 token using the
    asymmetric public key bytes as the HMAC secret. Our dispatcher picks
    the HS256 branch which verifies against SUPABASE_JWT_SECRET, not the
    public key, so the forgery fails. (PyJWT >= 2.14 refuses PEM and DER
    keys as HMAC secrets at encode time, so we sign the token by hand to
    make sure the rejection comes from *our* dispatch logic, not that
    safety net.)
    """
    import base64
    import hashlib
    import hmac
    import json

    from cryptography.hazmat.primitives import serialization

    _, pub = _make_es256_keypair()
    _patch_jwks(monkeypatch, pub)
    pub_der = pub.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    def b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64url(
        json.dumps({"sub": "evil", "aud": "authenticated", "exp": 9999999999}).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    signature = b64url(hmac.new(pub_der, signing_input, hashlib.sha256).digest())
    token = f"{header}.{payload}.{signature}"
    with pytest.raises(HTTPException) as exc:
        decode_supabase_jwt(token)
    assert exc.value.status_code == 401
