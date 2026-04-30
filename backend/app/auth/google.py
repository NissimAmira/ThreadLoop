"""Google ID token verification.

Google publishes its public signing keys at a fixed JWKS endpoint. We cache
the JWKS in-process for `_JWKS_CACHE_TTL_SECONDS` to avoid hammering Google
on every sign-in; on cache miss / expiry we fetch synchronously.

Failure semantics map to the OpenAPI contract:
    - JWKS unreachable                 -> JwksUnavailableError -> 503
    - Token signature invalid / expired
      / bad iss / aud mismatch         -> InvalidGoogleTokenError -> 401
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, cast

import httpx
from authlib.jose import JsonWebKey, jwt
from authlib.jose.errors import JoseError

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
_VALID_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})
_JWKS_CACHE_TTL_SECONDS = 60 * 60  # 1 hour — well below Google's key-rotation cadence


class JwksUnavailableError(Exception):
    """Google JWKS endpoint could not be reached. Maps to HTTP 503."""


class InvalidGoogleTokenError(Exception):
    """ID token failed signature, issuer, audience, or expiry validation."""


@dataclass(frozen=True)
class GoogleIdentity:
    """Verified claims from a Google ID token, narrowed to the fields we use."""

    sub: str
    email: str | None
    email_verified: bool
    name: str | None
    picture: str | None


class _JwksCache:
    """Thread-safe time-bounded JWKS cache.

    HTTP transport is injected so tests can hand in a `MockTransport` without
    monkey-patching the module. Production builds get a real `httpx.Client`.
    """

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport
        self._lock = threading.Lock()
        self._jwks_raw: dict[str, Any] | None = None
        self._fetched_at: float = 0.0

    def get(self, *, now: float | None = None) -> dict[str, Any]:
        current = now if now is not None else time.monotonic()
        with self._lock:
            if self._jwks_raw is not None and current - self._fetched_at < _JWKS_CACHE_TTL_SECONDS:
                return self._jwks_raw
            self._jwks_raw = self._fetch()
            self._fetched_at = current
            return self._jwks_raw

    def _fetch(self) -> dict[str, Any]:
        try:
            client_kwargs: dict[str, Any] = {"timeout": 5.0}
            if self._transport is not None:
                client_kwargs["transport"] = self._transport
            with httpx.Client(**client_kwargs) as client:
                resp = client.get(GOOGLE_JWKS_URL)
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise JwksUnavailableError(str(exc)) from exc
        if not isinstance(payload, dict):
            raise JwksUnavailableError("JWKS payload is not a JSON object")
        return payload

    def invalidate(self) -> None:
        with self._lock:
            self._jwks_raw = None
            self._fetched_at = 0.0


_default_cache = _JwksCache()


def get_default_cache() -> _JwksCache:
    """Process-wide JWKS cache. Tests construct their own via `_JwksCache`."""
    return _default_cache


def _verify_against_key_set(
    id_token: str,
    *,
    jwks_raw: dict[str, Any],
    now: float | None,
) -> dict[str, Any]:
    """Run JOSE signature + claim validation against `jwks_raw`. Raises
    `InvalidGoogleTokenError` on any cryptographic / semantic failure."""
    try:
        key_set = JsonWebKey.import_key_set(jwks_raw)
        claims = jwt.decode(id_token, key_set)
        claims.validate(now=int(now) if now is not None else None)
    except JoseError as exc:
        raise InvalidGoogleTokenError(f"token verification failed: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - authlib raises mixed exception types
        raise InvalidGoogleTokenError(f"token verification failed: {exc}") from exc
    return cast(dict[str, Any], claims)


def verify_google_id_token(
    id_token: str,
    *,
    expected_audience: str,
    cache: _JwksCache | None = None,
    now: float | None = None,
) -> GoogleIdentity:
    """Verify a Google-issued ID token end-to-end.

    Raises `JwksUnavailableError` if Google's JWKS can't be fetched and
    `InvalidGoogleTokenError` for any cryptographic / semantic failure.

    Rotation handling: Google rotates its signing keys roughly every few
    days. If the cached JWKS doesn't contain the key the token was signed
    with, the first verification fails with `InvalidGoogleTokenError`. We
    invalidate the cache and retry exactly once — that re-fetches a fresh
    JWKS that should contain the new key. Two failures in a row are treated
    as a genuinely bad token and propagated.
    """
    if not expected_audience:
        # Refuse to "verify" against an empty audience — that would silently
        # accept any well-formed token. Misconfiguration should fail loudly.
        raise InvalidGoogleTokenError("Google client ID is not configured")

    jwks_cache = cache if cache is not None else _default_cache
    jwks_raw = jwks_cache.get(now=now)

    try:
        claims = _verify_against_key_set(id_token, jwks_raw=jwks_raw, now=now)
    except InvalidGoogleTokenError:
        # Could be a genuine bad token, or could be that Google rotated its
        # signing keys and our cache is stale. Invalidate + retry once.
        jwks_cache.invalidate()
        jwks_raw = jwks_cache.get(now=now)
        claims = _verify_against_key_set(id_token, jwks_raw=jwks_raw, now=now)

    iss = claims.get("iss")
    if iss not in _VALID_ISSUERS:
        raise InvalidGoogleTokenError(f"unexpected issuer: {iss!r}")

    aud = claims.get("aud")
    # `aud` may legitimately be a list; require expected_audience to be in it.
    if isinstance(aud, str):
        aud_match = aud == expected_audience
    elif isinstance(aud, list):
        aud_match = expected_audience in aud
    else:
        aud_match = False
    if not aud_match:
        raise InvalidGoogleTokenError("audience does not match configured client ID")

    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise InvalidGoogleTokenError("token missing required `sub` claim")

    email_raw = claims.get("email")
    email = email_raw if isinstance(email_raw, str) and email_raw else None

    email_verified_raw = claims.get("email_verified", False)
    if isinstance(email_verified_raw, str):
        email_verified = email_verified_raw.lower() == "true"
    else:
        email_verified = bool(email_verified_raw)

    name_raw = claims.get("name")
    name = name_raw if isinstance(name_raw, str) and name_raw else None
    picture_raw = claims.get("picture")
    picture = picture_raw if isinstance(picture_raw, str) and picture_raw else None

    return GoogleIdentity(
        sub=sub,
        email=email,
        email_verified=email_verified,
        name=name,
        picture=picture,
    )
