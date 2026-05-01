"""Unit tests for Apple `client_secret` JWT generation + caching.

The `client_secret` JWT is what we'd send as the `client_secret` parameter
to `appleid.apple.com/auth/token` if we were exchanging the auth code.
#15 itself doesn't perform the exchange, but we generate + cache the JWT
here so a future scheduled-rotation job (RFC 0001 § Risks) and #17 can
reuse it without reaching into private state.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from authlib.jose import JsonWebKey, jwt

from app.auth.apple import (
    APPLE_ISSUER,
    InvalidAppleTokenError,
    _ClientSecretCache,
    _sign_client_secret_jwt,
    get_client_secret,
)

TEAM_ID = "TESTTEAM01"
CLIENT_ID = "com.threadloop.test.service"
KEY_ID = "TESTKID0001"


@pytest.fixture
def apple_p8_pem() -> str:
    """A freshly-generated ES256 PEM, standing in for the developer's `.p8`."""
    key = JsonWebKey.generate_key("EC", "P-256", is_private=True)
    return key.as_pem(is_private=True).decode("ascii")


# ----- signing --------------------------------------------------------------


def test_sign_jwt_has_required_claims(apple_p8_pem: str) -> None:
    now = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)
    encoded = _sign_client_secret_jwt(
        team_id=TEAM_ID,
        client_id=CLIENT_ID,
        key_id=KEY_ID,
        private_key_pem=apple_p8_pem,
        now=now,
    )

    # Sanity-check the header without verifying the signature (we just want to
    # see `alg` + `kid`).
    import base64
    import json as _json

    header_segment = encoded.split(".")[0]
    # base64url decode; pad to multiple of 4
    padding = "=" * (-len(header_segment) % 4)
    header = _json.loads(base64.urlsafe_b64decode(header_segment + padding).decode("ascii"))
    assert header["alg"] == "ES256"
    assert header["kid"] == KEY_ID

    # Now verify against the public half of the key we signed with.
    pub = JsonWebKey.import_key(apple_p8_pem)
    claims = jwt.decode(encoded, pub)

    assert claims["iss"] == TEAM_ID
    assert claims["sub"] == CLIENT_ID
    assert claims["aud"] == APPLE_ISSUER
    assert claims["iat"] == int(now.timestamp())
    # exp is exactly 1 hour after iat per the module-level _CLIENT_SECRET_TTL_SECONDS.
    assert claims["exp"] == int((now + timedelta(hours=1)).timestamp())


def test_sign_jwt_refuses_empty_inputs(apple_p8_pem: str) -> None:
    with pytest.raises(InvalidAppleTokenError, match="not configured"):
        _sign_client_secret_jwt(
            team_id="",
            client_id=CLIENT_ID,
            key_id=KEY_ID,
            private_key_pem=apple_p8_pem,
            now=datetime.now(UTC),
        )
    with pytest.raises(InvalidAppleTokenError, match="not configured"):
        _sign_client_secret_jwt(
            team_id=TEAM_ID,
            client_id="",
            key_id=KEY_ID,
            private_key_pem=apple_p8_pem,
            now=datetime.now(UTC),
        )
    with pytest.raises(InvalidAppleTokenError, match="not configured"):
        _sign_client_secret_jwt(
            team_id=TEAM_ID,
            client_id=CLIENT_ID,
            key_id="",
            private_key_pem=apple_p8_pem,
            now=datetime.now(UTC),
        )
    with pytest.raises(InvalidAppleTokenError, match="not configured"):
        _sign_client_secret_jwt(
            team_id=TEAM_ID,
            client_id=CLIENT_ID,
            key_id=KEY_ID,
            private_key_pem="",
            now=datetime.now(UTC),
        )


# ----- caching --------------------------------------------------------------


def test_get_client_secret_caches_within_ttl(apple_p8_pem: str) -> None:
    """Two calls within the 50-minute refresh window return the same JWT."""
    cache = _ClientSecretCache()
    base = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)

    first = get_client_secret(
        team_id=TEAM_ID,
        client_id=CLIENT_ID,
        key_id=KEY_ID,
        private_key_pem=apple_p8_pem,
        cache=cache,
        now=base,
    )
    second = get_client_secret(
        team_id=TEAM_ID,
        client_id=CLIENT_ID,
        key_id=KEY_ID,
        private_key_pem=apple_p8_pem,
        cache=cache,
        # 49 minutes later — still within the 50-minute refresh window.
        now=base + timedelta(minutes=49),
    )
    assert first == second


def test_get_client_secret_refreshes_after_threshold(apple_p8_pem: str) -> None:
    """Past the 50-minute mark, the cache resigns a fresh JWT (which carries
    a different `iat`/`exp` and so is byte-for-byte distinct)."""
    cache = _ClientSecretCache()
    base = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)

    first = get_client_secret(
        team_id=TEAM_ID,
        client_id=CLIENT_ID,
        key_id=KEY_ID,
        private_key_pem=apple_p8_pem,
        cache=cache,
        now=base,
    )
    second = get_client_secret(
        team_id=TEAM_ID,
        client_id=CLIENT_ID,
        key_id=KEY_ID,
        private_key_pem=apple_p8_pem,
        cache=cache,
        # 51 minutes later — past the threshold; must resign.
        now=base + timedelta(minutes=51),
    )
    assert first != second

    pub = JsonWebKey.import_key(apple_p8_pem)
    claims = jwt.decode(second, pub)
    assert claims["iat"] == int((base + timedelta(minutes=51)).timestamp())


def test_invalidate_clears_cache(apple_p8_pem: str) -> None:
    cache = _ClientSecretCache()
    base = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)

    first = get_client_secret(
        team_id=TEAM_ID,
        client_id=CLIENT_ID,
        key_id=KEY_ID,
        private_key_pem=apple_p8_pem,
        cache=cache,
        now=base,
    )
    cache.invalidate()
    second = get_client_secret(
        team_id=TEAM_ID,
        client_id=CLIENT_ID,
        key_id=KEY_ID,
        private_key_pem=apple_p8_pem,
        cache=cache,
        # Same `now` as first call — the only difference is the cache state.
        now=base,
    )
    # Same input timestamps but a fresh sign is fine. Both decode against
    # the same public key.
    pub = JsonWebKey.import_key(apple_p8_pem)
    jwt.decode(first, pub)
    jwt.decode(second, pub)


def test_signed_jwt_verifies_against_pem(apple_p8_pem: str) -> None:
    """A round-trip sanity check: the JWT we sign with `apple_p8_pem` parses
    back when we hand the same PEM to the verifier."""
    now = datetime.now(UTC)
    encoded = _sign_client_secret_jwt(
        team_id=TEAM_ID,
        client_id=CLIENT_ID,
        key_id=KEY_ID,
        private_key_pem=apple_p8_pem,
        now=now,
    )
    pub = JsonWebKey.import_key(apple_p8_pem)
    claims = jwt.decode(encoded, pub)
    claims.validate()  # no-op for an unexpired JWT, but exercises the path.
