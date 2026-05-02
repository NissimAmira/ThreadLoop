"""Unit tests for Apple `client_secret` JWT generation + caching.

The `client_secret` JWT is what we'd send as the `client_secret` parameter
to `appleid.apple.com/auth/token` if we were exchanging the auth code.
#15 itself doesn't perform the exchange, but we generate + cache the JWT
here so a future scheduled-rotation job (RFC 0001 § Risks) and #17 can
reuse it without reaching into private state.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

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


# ----- cache-key correctness (#34 item #1) ----------------------------------


def test_cache_invalidates_when_team_id_changes(apple_p8_pem: str) -> None:
    """Bug #34 item #1 regression: changing `team_id` mid-process must
    resign a fresh JWT instead of returning the cached one signed against
    the previous team. Before the fix the cache only checked age."""
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
    # Same `now`, same key, same client_id, same key_id — only team_id
    # changes. The previous behaviour returned `first` unchanged. The
    # post-fix behaviour signs a fresh JWT whose `iss` claim is the new
    # team.
    second = get_client_secret(
        team_id="OTHERTEAM2",
        client_id=CLIENT_ID,
        key_id=KEY_ID,
        private_key_pem=apple_p8_pem,
        cache=cache,
        now=base,
    )
    assert first != second, "rotated team_id must force a fresh JWT"

    pub = JsonWebKey.import_key(apple_p8_pem)
    first_claims = jwt.decode(first, pub)
    second_claims = jwt.decode(second, pub)
    assert first_claims["iss"] == TEAM_ID
    assert second_claims["iss"] == "OTHERTEAM2"


def test_cache_invalidates_when_private_key_pem_changes(
    apple_p8_pem: str,
) -> None:
    """Bug #34 item #1 regression: rotating the `.p8` PEM mid-process must
    resign with the new key, not keep serving a JWT signed against the
    previous one (which Apple would reject at the token endpoint)."""
    cache = _ClientSecretCache()
    base = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)

    other_key = JsonWebKey.generate_key("EC", "P-256", is_private=True)
    other_pem = other_key.as_pem(is_private=True).decode("ascii")

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
        private_key_pem=other_pem,
        cache=cache,
        now=base,
    )
    assert first != second, "rotated PEM must force a fresh JWT"

    # `first` verifies against the original key only; `second` verifies
    # against the rotated key only. If the cache had returned the stale
    # `first` for the second call, `second` would still verify against the
    # original key — the assertion below would fail.
    pub_orig = JsonWebKey.import_key(apple_p8_pem)
    pub_other = JsonWebKey.import_key(other_pem)
    jwt.decode(first, pub_orig)
    jwt.decode(second, pub_other)
    with pytest.raises(Exception):  # noqa: B017 - authlib raises various subtypes
        jwt.decode(second, pub_orig)


def test_cache_invalidates_when_client_id_changes(apple_p8_pem: str) -> None:
    """Symmetry with `team_id` / PEM rotation: rotating the Service ID
    (`client_id`, the JWT's `sub`) must resign rather than serve the cached
    JWT. A stale `sub` claim would cause Apple to reject the token at the
    `/auth/token` endpoint — symptom looks identical to the pre-fix
    `team_id` regression."""
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
        client_id="com.threadloop.test.OTHERSERVICE",
        key_id=KEY_ID,
        private_key_pem=apple_p8_pem,
        cache=cache,
        now=base,
    )
    assert first != second, "rotated client_id must force a fresh JWT"

    pub = JsonWebKey.import_key(apple_p8_pem)
    first_claims = jwt.decode(first, pub)
    second_claims = jwt.decode(second, pub)
    assert first_claims["sub"] == CLIENT_ID
    assert second_claims["sub"] == "com.threadloop.test.OTHERSERVICE"


def test_cache_invalidates_when_key_id_changes(apple_p8_pem: str) -> None:
    """Symmetry: rotating the `kid` header (Apple's key identifier) must
    resign. `kid` is what Apple uses to look up which public key to verify
    the JWT's signature with — a stale `kid` paired with a freshly-uploaded
    private key in the developer portal would fail verification at Apple's
    token endpoint."""
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
        key_id="OTHERKID0002",
        private_key_pem=apple_p8_pem,
        cache=cache,
        now=base,
    )
    assert first != second, "rotated key_id must force a fresh JWT"

    # `kid` lives in the JWT header, not the payload. Decode the header
    # segment directly (same trick used in `test_sign_jwt_has_required_claims`).
    import base64
    import json as _json

    def _header(encoded: str) -> dict[str, object]:
        seg = encoded.split(".")[0]
        padding = "=" * (-len(seg) % 4)
        return cast(
            dict[str, object],
            _json.loads(base64.urlsafe_b64decode(seg + padding).decode("ascii")),
        )

    assert _header(first)["kid"] == KEY_ID
    assert _header(second)["kid"] == "OTHERKID0002"


def test_cache_keeps_serving_unchanged_inputs_within_ttl(
    apple_p8_pem: str,
) -> None:
    """Sanity check the cache-key fix didn't accidentally turn the cache
    into a no-op: with all four inputs identical and within the TTL, the
    cached JWT is still returned."""
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
        now=base + timedelta(minutes=10),
    )
    assert first == second
