"""Session-issuance helpers shared by every SSO callback.

These primitives are deliberately provider-agnostic: a callback produces a
verified `User` row (existing or freshly inserted), then calls `issue_session`
to mint the access JWT, the opaque refresh token, the `refresh_tokens` row,
and to attach the cookie to the outbound response. #15 (Apple), #16 (Facebook),
and #17 (refresh) all reuse this module.

Refresh-token storage rules (RFC 0001 § Session model):
- The plaintext token is returned to the client (as a cookie value) but
  **never** persisted server-side. Only the HMAC-SHA-256 hash is stored.
- The HMAC key (`Settings.refresh_token_hmac_key`) is distinct from the JWT
  signing key — leaking one shouldn't let an attacker forge the other.
- HMAC over a hash like Argon2id is the deliberate choice here: refresh
  tokens are 256-bit URL-safe random values (not user-chosen), so the
  attack model is "leaked database, attacker tries to use the row" — not
  brute force. HMAC is constant-time-comparable, fast, and stateless;
  Argon2id's slow-by-design parameters add latency without buying anything
  for high-entropy inputs.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from authlib.jose import jwt
from fastapi import Response
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.models import RefreshToken, User

# Length of the random refresh-token value sent to the client. 32 bytes
# (256 bits) of entropy, base64url-encoded — well above any realistic
# brute-force budget.
_REFRESH_TOKEN_BYTES = 32

_JWT_ALG = "HS256"


@dataclass(frozen=True)
class IssuedSession:
    """Result of `issue_session`: everything a callback needs to build its
    OpenAPI `Session` response."""

    access_token: str
    access_token_expires_at: datetime
    refresh_token_plaintext: str
    refresh_token_row: RefreshToken


def hash_refresh_token(plaintext: str, *, hmac_key: str) -> bytes:
    """HMAC-SHA-256 of the plaintext refresh token, keyed with the server
    secret. Returns 32 raw bytes suitable for `refresh_tokens.token_hash`.
    """
    return hmac.new(
        hmac_key.encode("utf-8"),
        plaintext.encode("utf-8"),
        hashlib.sha256,
    ).digest()


def mint_access_token(
    user: User,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """Mint a short-lived HS256 access JWT for `user`. Returns the encoded
    token and its expiry timestamp."""
    issued_at = now if now is not None else datetime.now(UTC)
    expires_at = issued_at + timedelta(seconds=settings.access_token_ttl_seconds)
    payload = {
        "sub": str(user.id),
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        # `typ` lets #17's middleware reject link tokens presented as bearer
        # creds, even though both are signed with the same key.
        "typ": "access",
    }
    header = {"alg": _JWT_ALG}
    encoded = jwt.encode(header, payload, settings.jwt_signing_key)
    if isinstance(encoded, bytes):
        encoded = encoded.decode("ascii")
    return encoded, expires_at


def mint_refresh_token(
    user: User,
    *,
    db: DbSession,
    settings: Settings,
    now: datetime | None = None,
) -> tuple[str, RefreshToken]:
    """Generate a fresh opaque refresh token, persist its HMAC hash, and
    return both the plaintext (for the cookie) and the persisted row.

    The caller is responsible for committing the transaction. We `flush`
    so the row picks up DB-side defaults (id, issued_at) within the same
    SQLAlchemy session, but never `commit` here — the route owns the unit
    of work.
    """
    issued_at = now if now is not None else datetime.now(UTC)
    expires_at = issued_at + timedelta(days=settings.refresh_token_ttl_days)
    plaintext = secrets.token_urlsafe(_REFRESH_TOKEN_BYTES)
    token_hash = hash_refresh_token(plaintext, hmac_key=settings.refresh_token_hmac_key)

    row = RefreshToken(
        user_id=user.id,
        token_hash=token_hash,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    db.add(row)
    db.flush()
    return plaintext, row


def set_refresh_cookie(
    response: Response,
    plaintext: str,
    *,
    settings: Settings,
) -> None:
    """Attach the refresh-token cookie to `response` per RFC 0001:
    httpOnly, Secure (configurable for local dev), SameSite=Lax, scoped
    to the API origin. Lifetime mirrors the server-side row."""
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=plaintext,
        max_age=settings.refresh_token_ttl_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=cast(Literal["lax", "strict", "none"], settings.refresh_cookie_samesite),
        domain=settings.refresh_cookie_domain,
        path="/",
    )


def issue_session(
    user: User,
    *,
    db: DbSession,
    response: Response,
    settings: Settings,
    now: datetime | None = None,
) -> IssuedSession:
    """One-shot helper: mint access JWT + refresh token, persist the
    refresh row, set the cookie. Used by every successful callback.

    Flushes the new refresh-token row but does not commit; caller is
    responsible for committing the transaction. (Same contract as
    `mint_refresh_token`, propagated up.)
    """
    access_token, access_expires_at = mint_access_token(user, settings=settings, now=now)
    refresh_plaintext, refresh_row = mint_refresh_token(user, db=db, settings=settings, now=now)
    set_refresh_cookie(response, refresh_plaintext, settings=settings)
    return IssuedSession(
        access_token=access_token,
        access_token_expires_at=access_expires_at,
        refresh_token_plaintext=refresh_plaintext,
        refresh_token_row=refresh_row,
    )
