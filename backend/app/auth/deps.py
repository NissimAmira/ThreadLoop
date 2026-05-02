"""Auth dependencies for protected routes.

`require_user` is the slice-1 minimum: verify the access JWT, look up the
user, return the row. Returns 401 with the standard `Error` envelope on any
failure (missing header, malformed bearer scheme, bad signature, expired
token, wrong `typ`, deleted user). The 401 path matches `shared/openapi.yaml`
on the `/api/me` endpoint.

Role gates (`require_seller` / `require_buyer`) are deliberately NOT included
here — they're deferred to #37 per the 2026-05-01 vertical-slicing pivot.
They ship with their first consumers in the listings / transactions epics so
the dep AND the route that uses it land together. Adding them speculatively
here would mean shipping unused symbols that drift from their eventual
consumers.

Why a dedicated `require_user` instead of inlining JWT verification per
route: every protected route under listings / transactions / search will
reuse this exact failure mapping. Centralizing it ensures a single 401 shape
across the API and a single place to extend (e.g. for token-revocation
checks once we wire that in).
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any, cast

from authlib.jose import jwt
from authlib.jose.errors import JoseError
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session as DbSession

from app.config import Settings, get_settings
from app.db import get_db
from app.models import User

logger = logging.getLogger(__name__)

# `typ=access` is set by `mint_access_token` so the middleware can reject
# link tokens (which share the signing key) presented as bearer credentials.
_ACCESS_TOKEN_TYP = "access"


def require_auth_enabled(
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Gate every route that hangs off this dependency. Returns 404 (not 503)
    when the flag is off so an unauthenticated probe can't even tell the
    subsystem exists.

    Lives here rather than in `app.routers.auth` so `app.routers.users` can
    apply the same gate to `/api/me` without importing across the routers
    package (which would otherwise create a circular reference once `users`
    starts pulling more from the auth package). Both `/api/auth/*` and
    `/api/me` are part of the same RFC-0001 rollout switch — same flag, same
    response, same dep.
    """
    if not settings.auth_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


def _unauthorized(code: str, message: str) -> HTTPException:
    """Build the 401 envelope expected by the OpenAPI `Error` schema."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": code, "message": message},
    )


def _extract_bearer_token(request: Request) -> str:
    """Pull the bearer token out of the `Authorization` header.

    Surface 401 (not 422) on any structural problem — the wire contract for
    `/api/me` says missing/invalid creds → 401.
    """
    raw = request.headers.get("authorization")
    if not raw:
        raise _unauthorized("not_authenticated", "Missing Authorization header.")
    parts = raw.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise _unauthorized(
            "invalid_authorization_scheme",
            "Authorization header must use the Bearer scheme.",
        )
    token = parts[1].strip()
    if not token:
        raise _unauthorized("not_authenticated", "Bearer token is empty.")
    return token


def require_user(
    request: Request,
    db: Annotated[DbSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    """Dependency that resolves the bearer access JWT to a `User` row.

    Failure modes (all → 401):
      - missing / malformed Authorization header
      - JWT signature / expiry / structural failure (authlib raises JoseError
        for all of these; we collapse them into a single `invalid_token`
        response so we don't leak which check failed)
      - `typ` claim missing or not `access` (rejects link tokens replayed as
        access tokens, even though they share the signing key)
      - `sub` claim missing / not a valid UUID
      - user row not found (account deleted between token issue and use)
    """
    token = _extract_bearer_token(request)

    try:
        claims = jwt.decode(token, settings.jwt_signing_key)
        claims.validate()
    except JoseError as exc:
        logger.info("Access token rejected: %s", exc)
        raise _unauthorized("invalid_token", "Access token is invalid or expired.") from None
    except Exception as exc:  # noqa: BLE001 - authlib raises mixed exception types
        logger.info("Access token rejected: %s", exc)
        raise _unauthorized("invalid_token", "Access token is invalid or expired.") from None

    claims_dict = cast(dict[str, Any], claims)
    if claims_dict.get("typ") != _ACCESS_TOKEN_TYP:
        # A link token signed with the same key would otherwise pass JOSE
        # verification — `typ` is the only thing telling them apart.
        raise _unauthorized("invalid_token", "Access token is invalid or expired.")

    sub_raw = claims_dict.get("sub")
    if not isinstance(sub_raw, str) or not sub_raw:
        raise _unauthorized("invalid_token", "Access token is invalid or expired.")
    try:
        user_id = uuid.UUID(sub_raw)
    except ValueError:
        raise _unauthorized("invalid_token", "Access token is invalid or expired.") from None

    user = db.get(User, user_id)
    if user is None:
        # Token still cryptographically valid but the user was deleted (or
        # was never persisted — shouldn't happen on the happy path, but
        # we defend against it). Same 401 to avoid leaking "this user
        # used to exist" to a probe.
        raise _unauthorized("invalid_token", "Access token is invalid or expired.")
    return user
