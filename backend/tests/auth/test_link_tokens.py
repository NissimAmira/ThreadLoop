"""Unit tests for `app.auth.link` — the pending-link signed-JWT helpers."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.auth.link import (
    LinkTokenExpiredError,
    LinkTokenInvalidError,
    decode_link_token,
    issue_link_token,
)
from app.config import Settings


def _settings(ttl: int = 600) -> Settings:
    return Settings(jwt_signing_key="link-key", link_token_ttl_seconds=ttl)


def test_round_trip_recovers_all_claims() -> None:
    settings = _settings()
    existing_user_id = uuid.uuid4()
    token, expires_at = issue_link_token(
        existing_user_id=existing_user_id,
        new_provider="google",
        new_provider_user_id="google-sub-9",
        new_email="carol@example.com",
        settings=settings,
    )

    claims = decode_link_token(token, settings=settings)

    assert claims.existing_user_id == existing_user_id
    assert claims.new_provider == "google"
    assert claims.new_provider_user_id == "google-sub-9"
    assert claims.new_email == "carol@example.com"
    # 600s TTL ± a small skew for clock-tick during the test
    assert abs((claims.expires_at - expires_at).total_seconds()) < 2


def test_expired_token_raises() -> None:
    settings = _settings()
    past = datetime.now(UTC) - timedelta(hours=1)
    token, _ = issue_link_token(
        existing_user_id=uuid.uuid4(),
        new_provider="google",
        new_provider_user_id="google-sub-9",
        new_email="x@example.com",
        settings=settings,
        now=past,
    )

    with pytest.raises(LinkTokenExpiredError):
        decode_link_token(token, settings=settings)


def test_wrong_signing_key_rejected() -> None:
    issuer = _settings()
    verifier = Settings(jwt_signing_key="different-key", link_token_ttl_seconds=600)
    token, _ = issue_link_token(
        existing_user_id=uuid.uuid4(),
        new_provider="google",
        new_provider_user_id="google-sub-9",
        new_email="x@example.com",
        settings=issuer,
    )

    # authlib raises a JoseError subtype for bad signature; we let it
    # propagate, mirroring the production decoder.
    with pytest.raises(Exception):  # noqa: B017
        decode_link_token(token, settings=verifier)


def test_access_token_cannot_be_replayed_as_link_token() -> None:
    """`typ=access` JWTs share the signing key but must be rejected here."""
    from authlib.jose import jwt as authlib_jwt

    settings = _settings()
    now = int(time.time())
    forged = authlib_jwt.encode(
        {"alg": "HS256"},
        {
            "sub": str(uuid.uuid4()),
            "iat": now,
            "exp": now + 600,
            "typ": "access",
        },
        settings.jwt_signing_key,
    )
    if isinstance(forged, bytes):
        forged = forged.decode("ascii")

    with pytest.raises(LinkTokenInvalidError):
        decode_link_token(forged, settings=settings)
