"""Shared fixtures for auth tests.

The big one is `google_jwks_pair`: an in-memory RSA key pair plus matching
JWKS document and a sign-helper. Tests construct ID tokens with this helper
and feed them to the verifier (or the route) without touching Google live.
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

from app.auth import google as google_module
from app.auth.google import GOOGLE_JWKS_URL, _JwksCache


@dataclass
class GoogleJwksPair:
    """An RSA key pair, the JWKS exposing its public half, and a signing
    helper. Tests use the helper to mint tokens that pass `verify_google_id_token`."""

    private_jwk: JsonWebKey
    jwks: dict[str, Any]
    sign: Callable[[dict[str, Any]], str]


@pytest.fixture
def google_jwks_pair() -> GoogleJwksPair:
    private = JsonWebKey.generate_key("RSA", 2048, is_private=True)
    private_dict = private.as_dict(is_private=True)
    private_dict.setdefault("kid", "test-kid-1")
    private_dict.setdefault("alg", "RS256")
    private_dict.setdefault("use", "sig")

    public_dict = {
        k: v for k, v in private_dict.items() if k not in ("d", "p", "q", "dp", "dq", "qi")
    }
    public_dict["kid"] = private_dict["kid"]
    public_dict["alg"] = "RS256"
    public_dict["use"] = "sig"

    jwks = {"keys": [public_dict]}

    def sign(payload: dict[str, Any]) -> str:
        header = {"alg": "RS256", "kid": private_dict["kid"]}
        encoded = jwt.encode(header, payload, private_dict)
        return encoded.decode("ascii") if isinstance(encoded, bytes) else encoded

    return GoogleJwksPair(
        private_jwk=JsonWebKey.import_key(private_dict),
        jwks=jwks,
        sign=sign,
    )


def _jwks_transport(jwks: dict[str, Any], *, fail: bool = False) -> httpx.MockTransport:
    """An httpx transport that serves `jwks` for the Google JWKS URL.

    `fail=True` simulates the JWKS endpoint being unreachable (network error).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) != GOOGLE_JWKS_URL:
            return httpx.Response(404, json={"error": "unexpected url"})
        if fail:
            raise httpx.ConnectError("simulated outage", request=request)
        return httpx.Response(200, content=json.dumps(jwks))

    return httpx.MockTransport(handler)


@pytest.fixture
def jwks_transport_factory() -> Callable[..., httpx.MockTransport]:
    """Factory so tests can mint a transport with their own JWKS / failure mode."""
    return _jwks_transport


@pytest.fixture(autouse=True)
def _swap_google_jwks_cache(
    google_jwks_pair: GoogleJwksPair, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Default behaviour for any test in this directory: every call to
    `verify_google_id_token` hits an in-memory transport serving the test JWKS.

    Tests that want to exercise the unreachable-JWKS path swap in their own
    cache via the `with_failing_jwks` fixture below.
    """
    cache = _JwksCache(transport=_jwks_transport(google_jwks_pair.jwks))
    monkeypatch.setattr(google_module, "_default_cache", cache)
    yield


@pytest.fixture
def with_failing_jwks(monkeypatch: pytest.MonkeyPatch) -> Callable[[], None]:
    """Replace the default cache with one whose transport always errors."""

    def apply() -> None:
        cache = _JwksCache(transport=_jwks_transport({"keys": []}, fail=True))
        monkeypatch.setattr(google_module, "_default_cache", cache)

    return apply


@pytest.fixture
def google_id_token(google_jwks_pair: GoogleJwksPair) -> Callable[..., str]:
    """Builder for Google-shaped ID tokens. Defaults are valid; override per test."""
    now = int(time.time())

    def build(
        *,
        sub: str = "google-sub-12345",
        aud: str = "test-google-client-id.apps.googleusercontent.com",
        iss: str = "https://accounts.google.com",
        email: str | None = "user@example.com",
        email_verified: bool = True,
        name: str | None = "Test User",
        picture: str | None = "https://example.com/avatar.png",
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
        if name is not None:
            payload["name"] = name
        if picture is not None:
            payload["picture"] = picture
        if extra:
            payload.update(extra)
        return google_jwks_pair.sign(payload)

    return build
