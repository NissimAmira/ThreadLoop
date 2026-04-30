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


def test_each_issuance_carries_a_fresh_jti() -> None:
    """Two link tokens minted with identical inputs MUST have distinct
    `jti` claims. Without `jti`, a leaked link token is replayable for the
    full TTL; the claim is what lets #18 enforce single-use later (record
    consumed `jti`s in `consumed_link_tokens` / short-TTL Redis SETEX).
    """
    settings = _settings()
    existing_user_id = uuid.uuid4()
    kwargs = {
        "existing_user_id": existing_user_id,
        "new_provider": "google",
        "new_provider_user_id": "google-sub-1",
        "new_email": "carol@example.com",
        "settings": settings,
    }

    token_a, _ = issue_link_token(**kwargs)  # type: ignore[arg-type]
    token_b, _ = issue_link_token(**kwargs)  # type: ignore[arg-type]

    claims_a = decode_link_token(token_a, settings=settings)
    claims_b = decode_link_token(token_b, settings=settings)

    assert claims_a.jti and claims_b.jti, "every link token must carry a jti"
    assert claims_a.jti != claims_b.jti, "jti must be unique per issuance"


def test_link_token_missing_jti_is_rejected() -> None:
    """A token with no `jti` claim (e.g. forged with the legacy issuer
    pre-#14-cr-fix) must be rejected by `decode_link_token`."""
    from authlib.jose import jwt as authlib_jwt

    settings = _settings()
    now_ts = int(datetime.now(UTC).timestamp())
    forged = authlib_jwt.encode(
        {"alg": "HS256"},
        {
            "sub": str(uuid.uuid4()),
            "iat": now_ts,
            "exp": now_ts + 600,
            "typ": "link",
            "new_provider": "google",
            "new_provider_user_id": "google-sub-1",
            "new_email": "x@example.com",
            # Note: no `jti` claim.
        },
        settings.jwt_signing_key,
    )
    if isinstance(forged, bytes):
        forged = forged.decode("ascii")

    with pytest.raises(LinkTokenInvalidError, match="jti"):
        decode_link_token(forged, settings=settings)
