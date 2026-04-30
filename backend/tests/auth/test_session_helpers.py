"""Unit tests for `app.auth.session`. No DB; the helpers that touch the DB
are exercised end-to-end in `test_google_callback_integration.py`.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from authlib.jose import jwt
from fastapi import Response

from app.auth.session import (
    hash_refresh_token,
    mint_access_token,
    set_refresh_cookie,
)
from app.config import Settings
from app.models import User


def _user(provider: str = "google", sub: str = "google-sub-1") -> User:
    return User(
        id=uuid.uuid4(),
        provider=provider,
        provider_user_id=sub,
        email="alice@example.com",
        email_verified=True,
        display_name="Alice",
        avatar_url=None,
        can_sell=False,
        can_purchase=True,
    )


def test_hash_is_hmac_sha256_of_plaintext() -> None:
    settings = Settings(refresh_token_hmac_key="key-A")
    plaintext = "the-opaque-token"

    digest = hash_refresh_token(plaintext, hmac_key=settings.refresh_token_hmac_key)

    expected = hmac.new(b"key-A", plaintext.encode("utf-8"), hashlib.sha256).digest()
    assert digest == expected
    assert len(digest) == 32  # SHA-256 → 32 bytes


def test_hash_is_keyed_not_plain_sha() -> None:
    """Different HMAC keys must produce different hashes for the same plaintext."""
    plaintext = "the-opaque-token"
    h1 = hash_refresh_token(plaintext, hmac_key="key-A")
    h2 = hash_refresh_token(plaintext, hmac_key="key-B")
    plain_sha = hashlib.sha256(plaintext.encode("utf-8")).digest()
    assert h1 != h2
    assert h1 != plain_sha
    assert h2 != plain_sha


def test_mint_access_token_round_trips_and_carries_user_id() -> None:
    settings = Settings(jwt_signing_key="signing-key", access_token_ttl_seconds=900)
    user = _user()
    now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)

    token, expires_at = mint_access_token(user, settings=settings, now=now)
    claims = jwt.decode(token, settings.jwt_signing_key)

    assert claims["sub"] == str(user.id)
    assert claims["typ"] == "access"
    assert claims["iat"] == int(now.timestamp())
    assert claims["exp"] == int(now.timestamp()) + 900
    assert expires_at == now + timedelta(seconds=900)


def test_mint_access_token_signed_with_configured_key() -> None:
    settings = Settings(jwt_signing_key="signing-key")
    token, _ = mint_access_token(_user(), settings=settings)

    with pytest.raises(Exception):  # noqa: B017 - authlib raises various subtypes
        jwt.decode(token, "different-key")


def test_set_refresh_cookie_uses_secure_attributes() -> None:
    settings = Settings(
        refresh_cookie_name="refresh_token",
        refresh_cookie_secure=True,
        refresh_cookie_samesite="lax",
        refresh_token_ttl_days=30,
    )
    response = Response()
    set_refresh_cookie(response, "the-plaintext", settings=settings)

    set_cookie = response.headers.get("set-cookie", "").lower()
    assert "refresh_token=the-plaintext" in set_cookie
    assert "httponly" in set_cookie
    assert "secure" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "max-age=2592000" in set_cookie  # 30 days
    assert "path=/" in set_cookie


def test_set_refresh_cookie_skips_secure_for_local_dev() -> None:
    """Local HTTP dev sets `refresh_cookie_secure=false`; the Secure flag must
    not appear in that case."""
    settings = Settings(refresh_cookie_secure=False)
    response = Response()
    set_refresh_cookie(response, "the-plaintext", settings=settings)
    set_cookie = response.headers.get("set-cookie", "").lower()
    assert "secure" not in set_cookie
