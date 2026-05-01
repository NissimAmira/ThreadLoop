"""Apple ID token verification + `client_secret` JWT generation.

Apple's Sign in with Apple flow has two cryptographic moving parts that don't
exist in the Google flow:

1. **`client_secret` is itself a JWT.** Apple expects a short-lived JWT signed
   with the developer-team's private key (ES256, downloaded as a `.p8` file
   from the Apple Developer portal). We cache the signed JWT in-process for
   under its expiry so we don't resign on every request, and let the
   per-process state expire naturally — no scheduled rotation job (RFC 0001
   § Risks defers the rotation job; manual key rotation is sufficient for
   now since the cache is invalidated on process restart).

2. **ID tokens are signed with Apple's keys, served at a JWKS endpoint** —
   this part mirrors `app.auth.google` exactly (including the
   invalidate-and-retry-once rotation handler).

Failure semantics map to the OpenAPI contract:
    - JWKS unreachable                 -> JwksUnavailableError -> 503
    - Token signature invalid / expired
      / bad iss / aud mismatch         -> InvalidAppleTokenError -> 401
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
from authlib.jose import JsonWebKey, jwt
from authlib.jose.errors import JoseError

APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
APPLE_TOKEN_URL = "https://appleid.apple.com/auth/token"
APPLE_ISSUER = "https://appleid.apple.com"
_JWKS_CACHE_TTL_SECONDS = 60 * 60  # mirrors Google verifier; below Apple's rotation cadence

# `client_secret` JWT lifetime. Apple permits up to 6 months; we use 1 hour
# so a leaked `.p8` only buys an attacker 1 hour of impersonation, and so a
# manual rotation propagates within the cache window without any orchestration.
_CLIENT_SECRET_TTL_SECONDS = 60 * 60
# Refresh the cached `client_secret` JWT slightly before its `exp` to avoid
# racing requests against expiry. 50 minutes leaves a 10-minute safety margin.
_CLIENT_SECRET_REFRESH_AFTER_SECONDS = 50 * 60


class JwksUnavailableError(Exception):
    """Apple JWKS endpoint could not be reached. Maps to HTTP 503."""


class InvalidAppleTokenError(Exception):
    """ID token failed signature, issuer, audience, or expiry validation."""


@dataclass(frozen=True)
class AppleIdentity:
    """Verified claims from an Apple ID token, narrowed to the fields we use.

    Notes on Apple-specific fields:
    - `email` may be a relay address (`*@privaterelay.appleid.com`) when the
      user opts into Hide-My-Email. `is_private_email` is the signal.
    - `email` and the corresponding `email_verified` claim may be absent on
      sign-ins after the first; the route layer falls back to the existing
      `users` row in that case.
    """

    sub: str
    email: str | None
    email_verified: bool
    is_private_email: bool


class _JwksCache:
    """Thread-safe time-bounded JWKS cache. Mirrors `app.auth.google._JwksCache`.

    HTTP transport is injected so tests can hand in an `httpx.MockTransport`
    without monkey-patching the module. Production builds get a real client.
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
                resp = client.get(APPLE_JWKS_URL)
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
    `InvalidAppleTokenError` on any cryptographic / semantic failure."""
    try:
        key_set = JsonWebKey.import_key_set(jwks_raw)
        claims = jwt.decode(id_token, key_set)
        claims.validate(now=int(now) if now is not None else None)
    except JoseError as exc:
        raise InvalidAppleTokenError(f"token verification failed: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - authlib raises mixed exception types
        raise InvalidAppleTokenError(f"token verification failed: {exc}") from exc
    return cast(dict[str, Any], claims)


def verify_apple_id_token(
    id_token: str,
    *,
    expected_audience: str,
    cache: _JwksCache | None = None,
    now: float | None = None,
) -> AppleIdentity:
    """Verify an Apple-issued ID token end-to-end.

    Raises `JwksUnavailableError` if Apple's JWKS can't be fetched and
    `InvalidAppleTokenError` for any cryptographic / semantic failure.

    Rotation handling mirrors the Google verifier: if the cached JWKS doesn't
    contain the key the token was signed with, the first verification fails
    with `InvalidAppleTokenError`. We invalidate the cache and retry exactly
    once — that re-fetches a fresh JWKS that should contain the new key. Two
    failures in a row are treated as a genuinely bad token and propagated.
    """
    if not expected_audience:
        # Refuse to "verify" against an empty audience — that would silently
        # accept any well-formed token. Misconfiguration should fail loudly.
        raise InvalidAppleTokenError("Apple client ID is not configured")

    jwks_cache = cache if cache is not None else _default_cache
    jwks_raw = jwks_cache.get(now=now)

    try:
        claims = _verify_against_key_set(id_token, jwks_raw=jwks_raw, now=now)
    except InvalidAppleTokenError:
        # Could be a genuine bad token, or could be that Apple rotated its
        # signing keys and our cache is stale. Invalidate + retry once.
        jwks_cache.invalidate()
        jwks_raw = jwks_cache.get(now=now)
        claims = _verify_against_key_set(id_token, jwks_raw=jwks_raw, now=now)

    iss = claims.get("iss")
    if iss != APPLE_ISSUER:
        raise InvalidAppleTokenError(f"unexpected issuer: {iss!r}")

    aud = claims.get("aud")
    # Apple typically issues `aud` as a string, but the OIDC spec permits a
    # list — accept both forms for parity with the Google verifier.
    if isinstance(aud, str):
        aud_match = aud == expected_audience
    elif isinstance(aud, list):
        aud_match = expected_audience in aud
    else:
        aud_match = False
    if not aud_match:
        raise InvalidAppleTokenError("audience does not match configured client ID")

    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise InvalidAppleTokenError("token missing required `sub` claim")

    email_raw = claims.get("email")
    email = email_raw if isinstance(email_raw, str) and email_raw else None

    # Apple sends `email_verified` and `is_private_email` as either booleans
    # or the strings "true"/"false". Normalize both.
    email_verified = _coerce_bool(claims.get("email_verified", False))
    is_private_email = _coerce_bool(claims.get("is_private_email", False))

    return AppleIdentity(
        sub=sub,
        email=email,
        email_verified=email_verified,
        is_private_email=is_private_email,
    )


def _coerce_bool(value: Any) -> bool:
    """Accept Apple's mixed-type claim values: booleans, "true"/"false", or
    other truthy strings. Returns False on anything we don't recognise as
    truthy rather than guessing."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


# --- client_secret JWT generation -------------------------------------------


@dataclass(frozen=True)
class _CachedClientSecret:
    """In-process cache entry for a signed Apple `client_secret` JWT."""

    encoded: str
    issued_at: datetime


class _ClientSecretCache:
    """Process-wide cache of the `client_secret` JWT.

    Cached for `_CLIENT_SECRET_REFRESH_AFTER_SECONDS` (50 minutes), under the
    JWT's own 1-hour `exp`. Kept thread-safe so concurrent callbacks during
    a refresh don't double-sign.

    Manual rotation of the underlying `.p8` key requires either bouncing the
    process or calling `invalidate()` (e.g. from a future rotation job).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entry: _CachedClientSecret | None = None

    def get_or_create(
        self,
        *,
        team_id: str,
        client_id: str,
        key_id: str,
        private_key_pem: str,
        now: datetime | None = None,
    ) -> str:
        """Return a valid signed `client_secret` JWT, signing a fresh one if
        the cache is empty or the previous JWT is too close to expiry."""
        current = now if now is not None else datetime.now(UTC)
        with self._lock:
            if self._entry is not None:
                age = (current - self._entry.issued_at).total_seconds()
                if age < _CLIENT_SECRET_REFRESH_AFTER_SECONDS:
                    return self._entry.encoded
            encoded = _sign_client_secret_jwt(
                team_id=team_id,
                client_id=client_id,
                key_id=key_id,
                private_key_pem=private_key_pem,
                now=current,
            )
            self._entry = _CachedClientSecret(encoded=encoded, issued_at=current)
            return encoded

    def invalidate(self) -> None:
        with self._lock:
            self._entry = None


_default_client_secret_cache = _ClientSecretCache()


def get_default_client_secret_cache() -> _ClientSecretCache:
    """Process-wide `client_secret` cache. Tests construct their own."""
    return _default_client_secret_cache


def _sign_client_secret_jwt(
    *,
    team_id: str,
    client_id: str,
    key_id: str,
    private_key_pem: str,
    now: datetime,
) -> str:
    """Sign a single `client_secret` JWT per Apple's spec.

    Claims (Sign in with Apple — Generate and Validate Tokens):
        iss = Apple Team ID
        iat = now (epoch seconds)
        exp = iat + 1 hour
        aud = "https://appleid.apple.com"
        sub = the Service ID (the app's client identifier, AKA APPLE_CLIENT_ID)

    Header:
        alg = "ES256"
        kid = APPLE_KEY_ID

    Signed with the `.p8` private key downloaded from the Apple Developer
    portal — that file is a PEM-encoded ES256 key.
    """
    if not (team_id and client_id and key_id and private_key_pem):
        # Defense in depth — Settings already validates this when
        # `auth_enabled=True`, but a dev who flips the flag without setting
        # the keys deserves a loud failure rather than an opaque JOSE error.
        raise InvalidAppleTokenError("Apple client_secret signing key is not configured")
    issued_at = int(now.timestamp())
    expires_at = int((now + timedelta(seconds=_CLIENT_SECRET_TTL_SECONDS)).timestamp())
    header = {"alg": "ES256", "kid": key_id}
    payload = {
        "iss": team_id,
        "iat": issued_at,
        "exp": expires_at,
        "aud": APPLE_ISSUER,
        "sub": client_id,
    }
    encoded = jwt.encode(header, payload, private_key_pem)
    if isinstance(encoded, bytes):
        return encoded.decode("ascii")
    return cast(str, encoded)


def get_client_secret(
    *,
    team_id: str,
    client_id: str,
    key_id: str,
    private_key_pem: str,
    cache: _ClientSecretCache | None = None,
    now: datetime | None = None,
) -> str:
    """Return a cached or freshly-signed Apple `client_secret` JWT.

    Currently only used by the optional `code` exchange path; #15 itself does
    not need to round-trip through `appleid.apple.com/auth/token` since
    verifying the ID token is sufficient to establish identity. Exposing this
    helper now keeps #17 (or a future scheduled-rotation job) from needing
    to reach into private state.
    """
    target = cache if cache is not None else _default_client_secret_cache
    return target.get_or_create(
        team_id=team_id,
        client_id=client_id,
        key_id=key_id,
        private_key_pem=private_key_pem,
        now=now,
    )
