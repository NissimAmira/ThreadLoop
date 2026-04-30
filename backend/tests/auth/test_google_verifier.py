"""Unit tests for `verify_google_id_token`. JWKS is mocked via
`httpx.MockTransport`; nothing in this file ever reaches the network.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest

from app.auth import google as google_module
from app.auth.google import (
    InvalidGoogleTokenError,
    JwksUnavailableError,
    _JwksCache,
    verify_google_id_token,
)

GOOGLE_AUD = "test-google-client-id.apps.googleusercontent.com"


def test_happy_path_extracts_claims(google_id_token: Callable[..., str]) -> None:
    token = google_id_token(
        sub="abc-123",
        email="alice@example.com",
        email_verified=True,
        name="Alice",
        picture="https://cdn.example/a.png",
    )

    identity = verify_google_id_token(token, expected_audience=GOOGLE_AUD)

    assert identity.sub == "abc-123"
    assert identity.email == "alice@example.com"
    assert identity.email_verified is True
    assert identity.name == "Alice"
    assert identity.picture == "https://cdn.example/a.png"


def test_email_verified_string_true_is_normalized(google_id_token: Callable[..., str]) -> None:
    """Some Google tokens send `email_verified` as the string `"true"`."""
    token = google_id_token(extra={"email_verified": "true"})
    identity = verify_google_id_token(token, expected_audience=GOOGLE_AUD)
    assert identity.email_verified is True


def test_apostrophe_iss_accounts_dot_google_dot_com_is_accepted(
    google_id_token: Callable[..., str],
) -> None:
    token = google_id_token(iss="accounts.google.com")
    identity = verify_google_id_token(token, expected_audience=GOOGLE_AUD)
    assert identity.sub  # any sub means we accepted the iss


def test_unexpected_issuer_rejected(google_id_token: Callable[..., str]) -> None:
    token = google_id_token(iss="https://evil.example/")
    with pytest.raises(InvalidGoogleTokenError, match="issuer"):
        verify_google_id_token(token, expected_audience=GOOGLE_AUD)


def test_audience_mismatch_rejected(google_id_token: Callable[..., str]) -> None:
    token = google_id_token(aud="wrong-aud")
    with pytest.raises(InvalidGoogleTokenError, match="audience"):
        verify_google_id_token(token, expected_audience=GOOGLE_AUD)


def test_audience_list_with_match_accepted(google_id_token: Callable[..., str]) -> None:
    """Google may return aud as a list; we accept if expected is in it."""
    token = google_id_token(aud=[GOOGLE_AUD, "other-client"])
    identity = verify_google_id_token(token, expected_audience=GOOGLE_AUD)
    assert identity.sub


def test_empty_expected_audience_rejected_loudly(google_id_token: Callable[..., str]) -> None:
    """If GOOGLE_CLIENT_ID is unset, refuse to verify rather than accept everything."""
    token = google_id_token()
    with pytest.raises(InvalidGoogleTokenError, match="not configured"):
        verify_google_id_token(token, expected_audience="")


def test_expired_token_rejected(google_id_token: Callable[..., str]) -> None:
    past = int(time.time()) - 7200
    token = google_id_token(iat=past, exp=past + 60)
    with pytest.raises(InvalidGoogleTokenError):
        verify_google_id_token(token, expected_audience=GOOGLE_AUD)


def test_tampered_signature_rejected(google_id_token: Callable[..., str]) -> None:
    token = google_id_token()
    parts = token.split(".")
    # Flip a byte in the signature segment without changing length.
    sig = parts[2]
    swapped_char = "A" if sig[0] != "A" else "B"
    bad = ".".join([parts[0], parts[1], swapped_char + sig[1:]])
    with pytest.raises(InvalidGoogleTokenError):
        verify_google_id_token(bad, expected_audience=GOOGLE_AUD)


def test_garbage_token_rejected() -> None:
    with pytest.raises(InvalidGoogleTokenError):
        verify_google_id_token("not-a-jwt", expected_audience=GOOGLE_AUD)


def test_missing_sub_rejected(
    google_jwks_pair: object,  # noqa: ARG001 - keeps fixture wiring stable
    google_id_token: Callable[..., str],
) -> None:
    """Strip `sub` post-hoc by signing a payload without it."""
    from tests.auth.conftest import GoogleJwksPair  # local import to avoid cycle

    pair: GoogleJwksPair = google_jwks_pair  # type: ignore[assignment]
    now = int(time.time())
    token = pair.sign(
        {
            "aud": GOOGLE_AUD,
            "iss": "https://accounts.google.com",
            "iat": now,
            "exp": now + 3600,
        }
    )
    with pytest.raises(InvalidGoogleTokenError, match="sub"):
        verify_google_id_token(token, expected_audience=GOOGLE_AUD)


def test_jwks_unreachable_raises_specific_error(
    with_failing_jwks: Callable[[], None],
    google_id_token: Callable[..., str],
) -> None:
    with_failing_jwks()
    token = google_id_token()
    with pytest.raises(JwksUnavailableError):
        verify_google_id_token(token, expected_audience=GOOGLE_AUD)


def test_jwks_cache_is_used_within_ttl(
    google_jwks_pair: object,
    monkeypatch: pytest.MonkeyPatch,
    google_id_token: Callable[..., str],
) -> None:
    """Two verifications in succession should hit the JWKS endpoint once."""
    import httpx

    from tests.auth.conftest import GoogleJwksPair

    pair: GoogleJwksPair = google_jwks_pair  # type: ignore[assignment]
    fetch_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal fetch_count
        fetch_count += 1
        return httpx.Response(200, json=pair.jwks)

    cache = _JwksCache(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(google_module, "_default_cache", cache)

    token = google_id_token()
    verify_google_id_token(token, expected_audience=GOOGLE_AUD)
    verify_google_id_token(token, expected_audience=GOOGLE_AUD)
    assert fetch_count == 1, "JWKS should be cached across calls within TTL"
