"""Pending-link tokens: short-lived signed JWTs that remember "user X tried to
sign in with provider B, but provider A's row owns this email — they must
confirm by re-authenticating with A".

Storage choice: **stateless signed JWT** keyed with `Settings.jwt_signing_key`.
Alternatives considered:

- Redis short-TTL entry — adds infra dependency to the auth path; #17 has not
  yet wired Redis into the app layer (only health-checked).
- Dedicated `link_intents` table — durable but overkill for a 5–10 minute
  flow that fully self-resolves on the client; would also need a sweeper.

A signed JWT carries everything #18's `POST /api/auth/link` needs: the
existing-user id (the one the client must re-auth into), the second-provider
identity that should be linked, and a hard expiry. No server-side state to
clean up; revocation isn't a concern at this TTL.

Token shape (HS256):
    sub  = existing user id (UUID string)
    iat  = issued-at (epoch seconds)
    exp  = expiry (epoch seconds)
    typ  = "link"
    new_provider          = "google" | "apple" | "facebook"
    new_provider_user_id  = the `sub` from the second-provider token
    new_email             = email from the second-provider token (verified)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from authlib.jose import jwt
from authlib.jose.errors import JoseError

from app.config import Settings

_JWT_ALG = "HS256"
_TOKEN_TYPE = "link"


@dataclass(frozen=True)
class LinkTokenClaims:
    existing_user_id: uuid.UUID
    new_provider: str
    new_provider_user_id: str
    new_email: str
    expires_at: datetime


def issue_link_token(
    *,
    existing_user_id: uuid.UUID,
    new_provider: str,
    new_provider_user_id: str,
    new_email: str,
    settings: Settings,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """Mint a pending-link token. Returns (encoded_jwt, expires_at)."""
    issued_at = now if now is not None else datetime.now(UTC)
    expires_at = issued_at + timedelta(seconds=settings.link_token_ttl_seconds)
    payload = {
        "sub": str(existing_user_id),
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "typ": _TOKEN_TYPE,
        "new_provider": new_provider,
        "new_provider_user_id": new_provider_user_id,
        "new_email": new_email,
    }
    header = {"alg": _JWT_ALG}
    encoded = jwt.encode(header, payload, settings.jwt_signing_key)
    if isinstance(encoded, bytes):
        encoded = encoded.decode("ascii")
    return encoded, expires_at


def decode_link_token(
    token: str,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> LinkTokenClaims:
    """Verify signature + expiry on a pending-link token. Raises
    `JoseError` (signature / parse) or `LinkTokenExpiredError` (expiry).
    Consumed by #18 (`POST /api/auth/link`)."""
    try:
        claims = jwt.decode(token, settings.jwt_signing_key)
    except JoseError:
        raise
    if claims.get("typ") != _TOKEN_TYPE:
        raise LinkTokenInvalidError("token is not a link token")

    current = now if now is not None else datetime.now(UTC)
    exp = claims.get("exp")
    if exp is None or current.timestamp() >= float(exp):
        raise LinkTokenExpiredError("link token expired")

    try:
        existing_user_id = uuid.UUID(str(claims["sub"]))
    except (KeyError, ValueError) as exc:
        raise LinkTokenInvalidError("link token missing or malformed sub") from exc

    expires_at = datetime.fromtimestamp(float(exp), tz=UTC)
    return LinkTokenClaims(
        existing_user_id=existing_user_id,
        new_provider=str(claims["new_provider"]),
        new_provider_user_id=str(claims["new_provider_user_id"]),
        new_email=str(claims["new_email"]),
        expires_at=expires_at,
    )


class LinkTokenInvalidError(Exception):
    """Token failed structural / claim validation."""


class LinkTokenExpiredError(Exception):
    """Token signature is valid but its expiry has passed."""
