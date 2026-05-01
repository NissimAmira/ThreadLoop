"""Unit tests for `verify_apple_id_token`. JWKS is mocked via
`httpx.MockTransport`; nothing in this file ever reaches the network.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from authlib.jose import JsonWebKey, jwt

from app.auth import apple as apple_module
from app.auth.apple import (
    APPLE_JWKS_URL,
    InvalidAppleTokenError,
    JwksUnavailableError,
    _JwksCache,
    verify_apple_id_token,
)

APPLE_AUD = "com.threadloop.app"  # the Service ID we'd register in production


@dataclass
class AppleJwksPair:
    private_jwk: JsonWebKey
    jwks: dict[str, Any]
    sign: Callable[[dict[str, Any]], str]


def _make_apple_pair(kid: str = "apple-test-kid") -> AppleJwksPair:
    """Apple uses RS256 for ID tokens (ES256 is reserved for the client_secret
    JWT we sign ourselves). Build an RSA pair + JWKS that mirrors that."""
    private = JsonWebKey.generate_key("RSA", 2048, is_private=True)
    private_dict = private.as_dict(is_private=True)
    private_dict["kid"] = kid
    private_dict["alg"] = "RS256"
    private_dict["use"] = "sig"

    public_dict = {
        k: v for k, v in private_dict.items() if k not in ("d", "p", "q", "dp", "dq", "qi")
    }
    public_dict["kid"] = kid
    public_dict["alg"] = "RS256"
    public_dict["use"] = "sig"

    jwks = {"keys": [public_dict]}

    def sign(payload: dict[str, Any]) -> str:
        header = {"alg": "RS256", "kid": kid}
        encoded = jwt.encode(header, payload, private_dict)
        return encoded.decode("ascii") if isinstance(encoded, bytes) else encoded

    return AppleJwksPair(
        private_jwk=JsonWebKey.import_key(private_dict),
        jwks=jwks,
        sign=sign,
    )


def _apple_jwks_transport(jwks: dict[str, Any], *, fail: bool = False) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) != APPLE_JWKS_URL:
            return httpx.Response(404, json={"error": "unexpected url"})
        if fail:
            raise httpx.ConnectError("simulated outage", request=request)
        return httpx.Response(200, content=json.dumps(jwks))

    return httpx.MockTransport(handler)


@pytest.fixture
def apple_jwks_pair() -> AppleJwksPair:
    return _make_apple_pair()


@pytest.fixture(autouse=True)
def _swap_apple_jwks_cache(
    apple_jwks_pair: AppleJwksPair, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    cache = _JwksCache(transport=_apple_jwks_transport(apple_jwks_pair.jwks))
    monkeypatch.setattr(apple_module, "_default_cache", cache)
    yield


@pytest.fixture
def with_failing_apple_jwks(monkeypatch: pytest.MonkeyPatch) -> Callable[[], None]:
    def apply() -> None:
        cache = _JwksCache(transport=_apple_jwks_transport({"keys": []}, fail=True))
        monkeypatch.setattr(apple_module, "_default_cache", cache)

    return apply


@pytest.fixture
def apple_id_token(apple_jwks_pair: AppleJwksPair) -> Callable[..., str]:
    """Builder for Apple-shaped ID tokens. Defaults are valid; override per test."""
    now = int(time.time())

    def build(
        *,
        sub: str = "apple-sub-12345",
        aud: str = APPLE_AUD,
        iss: str = "https://appleid.apple.com",
        email: str | None = "user@example.com",
        email_verified: bool | str = True,
        is_private_email: bool | str | None = False,
        iat: int | None = None,
        exp: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "sub": sub,
            "aud": aud,
            "iss": iss,
            "iat": iat if iat is not None else now,
            "exp": exp if exp is not None else now + 3600,
        }
        if email is not None:
            payload["email"] = email
            payload["email_verified"] = email_verified
            if is_private_email is not None:
                payload["is_private_email"] = is_private_email
        if extra:
            payload.update(extra)
        return apple_jwks_pair.sign(payload)

    return build


# ----- happy path -----------------------------------------------------------


def test_happy_path_extracts_claims(apple_id_token: Callable[..., str]) -> None:
    token = apple_id_token(
        sub="apple-sub-1",
        email="alice@example.com",
        email_verified=True,
        is_private_email=False,
    )

    identity = verify_apple_id_token(token, expected_audience=APPLE_AUD)

    assert identity.sub == "apple-sub-1"
    assert identity.email == "alice@example.com"
    assert identity.email_verified is True
    assert identity.is_private_email is False


def test_relay_email_flagged_as_private(apple_id_token: Callable[..., str]) -> None:
    token = apple_id_token(
        sub="apple-sub-relay",
        email="abc123@privaterelay.appleid.com",
        email_verified=True,
        is_private_email=True,
    )
    identity = verify_apple_id_token(token, expected_audience=APPLE_AUD)
    assert identity.is_private_email is True
    assert identity.email == "abc123@privaterelay.appleid.com"


def test_email_verified_string_true_normalized(apple_id_token: Callable[..., str]) -> None:
    """Apple ships `email_verified` as either bool or "true"/"false" string."""
    token = apple_id_token(email_verified="true")
    identity = verify_apple_id_token(token, expected_audience=APPLE_AUD)
    assert identity.email_verified is True


def test_is_private_email_string_true_normalized(apple_id_token: Callable[..., str]) -> None:
    token = apple_id_token(is_private_email="true")
    identity = verify_apple_id_token(token, expected_audience=APPLE_AUD)
    assert identity.is_private_email is True


def test_email_absent_on_subsequent_signin(apple_id_token: Callable[..., str]) -> None:
    """Apple omits `email` on sign-ins after the first; the verifier must
    not crash and must surface email=None / email_verified=False."""
    token = apple_id_token(email=None)
    identity = verify_apple_id_token(token, expected_audience=APPLE_AUD)
    assert identity.email is None
    assert identity.email_verified is False
    assert identity.is_private_email is False


# ----- error paths ----------------------------------------------------------


def test_unexpected_issuer_rejected(apple_id_token: Callable[..., str]) -> None:
    token = apple_id_token(iss="https://accounts.google.com")
    with pytest.raises(InvalidAppleTokenError, match="issuer"):
        verify_apple_id_token(token, expected_audience=APPLE_AUD)


def test_audience_mismatch_rejected(apple_id_token: Callable[..., str]) -> None:
    token = apple_id_token(aud="some-other-service-id")
    with pytest.raises(InvalidAppleTokenError, match="audience"):
        verify_apple_id_token(token, expected_audience=APPLE_AUD)


def test_audience_list_with_match_accepted(apple_id_token: Callable[..., str]) -> None:
    token = apple_id_token(aud=[APPLE_AUD, "other-app"])
    identity = verify_apple_id_token(token, expected_audience=APPLE_AUD)
    assert identity.sub


def test_empty_expected_audience_rejected_loudly(apple_id_token: Callable[..., str]) -> None:
    """If APPLE_CLIENT_ID is unset, refuse to verify rather than accept any token."""
    token = apple_id_token()
    with pytest.raises(InvalidAppleTokenError, match="not configured"):
        verify_apple_id_token(token, expected_audience="")


def test_expired_token_rejected(apple_id_token: Callable[..., str]) -> None:
    past = int(time.time()) - 7200
    token = apple_id_token(iat=past, exp=past + 60)
    with pytest.raises(InvalidAppleTokenError):
        verify_apple_id_token(token, expected_audience=APPLE_AUD)


def test_tampered_signature_rejected(apple_id_token: Callable[..., str]) -> None:
    token = apple_id_token()
    parts = token.split(".")
    sig = parts[2]
    swapped_char = "A" if sig[0] != "A" else "B"
    bad = ".".join([parts[0], parts[1], swapped_char + sig[1:]])
    with pytest.raises(InvalidAppleTokenError):
        verify_apple_id_token(bad, expected_audience=APPLE_AUD)


def test_garbage_token_rejected() -> None:
    with pytest.raises(InvalidAppleTokenError):
        verify_apple_id_token("not-a-jwt", expected_audience=APPLE_AUD)


def test_missing_sub_rejected(apple_jwks_pair: AppleJwksPair) -> None:
    now = int(time.time())
    token = apple_jwks_pair.sign(
        {
            "aud": APPLE_AUD,
            "iss": "https://appleid.apple.com",
            "iat": now,
            "exp": now + 3600,
            "email": "noone@example.com",
        }
    )
    with pytest.raises(InvalidAppleTokenError, match="sub"):
        verify_apple_id_token(token, expected_audience=APPLE_AUD)


def test_jwks_unreachable_raises_specific_error(
    with_failing_apple_jwks: Callable[[], None],
    apple_id_token: Callable[..., str],
) -> None:
    with_failing_apple_jwks()
    token = apple_id_token()
    with pytest.raises(JwksUnavailableError):
        verify_apple_id_token(token, expected_audience=APPLE_AUD)


# ----- caching + rotation ---------------------------------------------------


def test_jwks_cache_is_used_within_ttl(
    apple_jwks_pair: AppleJwksPair,
    monkeypatch: pytest.MonkeyPatch,
    apple_id_token: Callable[..., str],
) -> None:
    fetch_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal fetch_count
        fetch_count += 1
        return httpx.Response(200, json=apple_jwks_pair.jwks)

    cache = _JwksCache(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(apple_module, "_default_cache", cache)

    token = apple_id_token()
    verify_apple_id_token(token, expected_audience=APPLE_AUD)
    verify_apple_id_token(token, expected_audience=APPLE_AUD)
    assert fetch_count == 1, "JWKS should be cached across calls within TTL"


def test_rotation_invalidates_stale_cache_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apple, like Google, rotates its JWKS keys. If a token is signed by the
    *new* key but our cache holds the *old* JWKS, the first verification
    fails — we must invalidate, refetch, and retry once before giving up."""
    old_pair = _make_apple_pair("kid-old")
    new_pair = _make_apple_pair("kid-new")

    now_ts = int(time.time())
    token_signed_by_new = new_pair.sign(
        {
            "sub": "apple-rot-1",
            "aud": APPLE_AUD,
            "iss": "https://appleid.apple.com",
            "iat": now_ts,
            "exp": now_ts + 3600,
            "email": "rot@example.com",
            "email_verified": True,
        }
    )

    fetch_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal fetch_count
        fetch_count += 1
        if fetch_count == 1:
            return httpx.Response(200, content=json.dumps(old_pair.jwks))
        return httpx.Response(200, content=json.dumps(new_pair.jwks))

    cache = _JwksCache(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(apple_module, "_default_cache", cache)

    identity = verify_apple_id_token(token_signed_by_new, expected_audience=APPLE_AUD)

    assert identity.sub == "apple-rot-1"
    assert fetch_count == 2, "verifier must invalidate the stale cache and refetch once"


def test_rotation_retry_does_not_paper_over_genuinely_bad_tokens(
    apple_id_token: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the second JWKS fetch returns the same keys, a tampered signature
    must still be rejected — the retry-on-failure path must not become a
    silent acceptor."""
    token = apple_id_token()
    parts = token.split(".")
    swapped_char = "A" if parts[2][0] != "A" else "B"
    bad = ".".join([parts[0], parts[1], swapped_char + parts[2][1:]])

    autouse_cache = apple_module._default_cache  # type: ignore[attr-defined]
    jwks = autouse_cache.get()

    fetch_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal fetch_count
        fetch_count += 1
        return httpx.Response(200, content=json.dumps(jwks))

    cache = _JwksCache(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(apple_module, "_default_cache", cache)

    with pytest.raises(InvalidAppleTokenError):
        verify_apple_id_token(bad, expected_audience=APPLE_AUD)

    assert fetch_count == 2
