"""Auth callback dispatcher + session lifecycle routes.

Wire format follows the auth section of `shared/openapi.yaml`. The provider
callbacks (`google`/`apple`/`facebook`) shipped in #14/#15/#16. The session
lifecycle routes — refresh, logout, /me — ship in #17 (slice 1 BE half).

The whole router is gated behind `Settings.auth_enabled` per RFC 0001 §
Rollout plan step 1: every route returns 404 while the flag is off so we can
land the implementation environment-by-environment.

The 'find-or-create user' + 'detect cross-provider email collision' logic
lives here; everything cryptographic / network-bound is in `app.auth.google`
/ `app.auth.apple` / `app.auth.facebook` (token verify), `app.auth.session`
(mint + cookie), and `app.auth.link` (pending-link token). Bearer-JWT
validation for protected routes lives in `app.auth.deps.require_user`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session as DbSession

from app.auth.apple import (
    InvalidAppleTokenError,
    verify_apple_id_token,
)
from app.auth.apple import JwksUnavailableError as AppleJwksUnavailableError
from app.auth.facebook import (
    GraphApiUnavailableError,
    InvalidFacebookTokenError,
    verify_facebook_access_token,
)
from app.auth.google import (
    InvalidGoogleTokenError,
    JwksUnavailableError,
    verify_google_id_token,
)
from app.auth.link import issue_link_token
from app.auth.schemas import (
    AppleSsoCallbackInput,
    AuthProvider,
    FacebookSsoCallbackInput,
    GoogleSsoCallbackInput,
    Session,
    UserOut,
)
from app.auth.session import (
    hash_refresh_token,
    issue_session,
    mint_access_token,
    mint_refresh_token,
    set_refresh_cookie,
)
from app.config import Settings, get_settings
from app.db import get_db
from app.models import RefreshToken, User

logger = logging.getLogger(__name__)


def require_auth_enabled(
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Gate every route on this router. Returns 404 (not 503) when the flag
    is off so an unauthenticated probe can't even tell the subsystem exists.

    Attached as a router-level dependency rather than baked into each handler
    so OpenAPI generation still describes the routes — `/docs` stays honest
    about what the API surface will look like once the flag is flipped.
    """
    if not settings.auth_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


router = APIRouter(
    prefix="/auth",
    tags=["auth"],
    dependencies=[Depends(require_auth_enabled)],
)

_KNOWN_PROVIDERS: frozenset[str] = frozenset({"google", "apple", "facebook"})


def _http_error(code: str, message: str, http_status: int) -> HTTPException:
    """Build an HTTPException whose body matches the OpenAPI `Error` schema."""
    return HTTPException(status_code=http_status, detail={"code": code, "message": message})


@router.post(
    "/{provider}/callback",
    response_model=Session,
    response_model_exclude_none=True,
    summary="Exchange a provider credential for a ThreadLoop session",
)
def sso_callback(
    response: Response,
    body: Annotated[dict[str, Any], Body(...)],
    provider: Literal["google", "apple", "facebook"] = Path(...),
    db: DbSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Session:
    """Dispatch by provider name. The body is intentionally typed as a raw
    dict here so we can return 404 for not-yet-implemented providers BEFORE
    spending Pydantic cycles validating a body shape that doesn't match the
    branch we're about to take. Each branch validates its own body via the
    matching Pydantic schema as the first thing it does.
    """
    if provider not in _KNOWN_PROVIDERS:
        # Unreachable while `provider` is constrained by Literal, but kept
        # as a defense in depth and to match the OpenAPI 404 contract.
        raise _http_error(
            "unknown_provider",
            f"Unknown auth provider {provider!r}.",
            status.HTTP_404_NOT_FOUND,
        )

    if provider == "google":
        try:
            google_body = GoogleSsoCallbackInput.model_validate(body)
        except ValidationError as exc:
            # Surface as 422 (FastAPI's default for body validation), matching
            # the behaviour FastAPI would have produced if we'd typed `body`
            # as `GoogleSsoCallbackInput` directly.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=exc.errors(),
            ) from None
        return _handle_google_callback(
            body=google_body, response=response, db=db, settings=settings
        )

    if provider == "apple":
        try:
            apple_body = AppleSsoCallbackInput.model_validate(body)
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=exc.errors(),
            ) from None
        return _handle_apple_callback(body=apple_body, response=response, db=db, settings=settings)

    # provider == "facebook" — the only remaining branch given the Literal
    # constraint on `provider` and the membership check above.
    try:
        facebook_body = FacebookSsoCallbackInput.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(),
        ) from None
    return _handle_facebook_callback(
        body=facebook_body, response=response, db=db, settings=settings
    )


def _handle_google_callback(
    *,
    body: GoogleSsoCallbackInput,
    response: Response,
    db: DbSession,
    settings: Settings,
) -> Session:
    try:
        identity = verify_google_id_token(
            body.id_token,
            expected_audience=settings.google_client_id,
        )
    except JwksUnavailableError:
        logger.warning("Google JWKS unavailable; returning 503")
        raise _http_error(
            "jwks_unavailable",
            "Google JWKS endpoint is unreachable; please retry.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from None
    except InvalidGoogleTokenError as exc:
        # Don't echo the upstream message — it can carry token contents.
        logger.info("Google ID token rejected: %s", exc)
        raise _http_error(
            "invalid_token",
            "Google ID token is invalid or expired.",
            status.HTTP_401_UNAUTHORIZED,
        ) from None

    existing = db.execute(
        select(User).where(User.provider == "google", User.provider_user_id == identity.sub)
    ).scalar_one_or_none()

    if existing is None and identity.email and identity.email_verified:
        collision = db.execute(
            select(User).where(
                User.email == identity.email,
                User.email_verified.is_(True),
                User.provider != "google",
            )
        ).scalar_one_or_none()
        if collision is not None:
            link_token, _ = issue_link_token(
                existing_user_id=collision.id,
                new_provider="google",
                new_provider_user_id=identity.sub,
                new_email=identity.email,
                settings=settings,
            )
            return Session(
                link_required=True,
                link_provider=cast(AuthProvider, collision.provider),
                link_token=link_token,
            )

    if existing is None:
        user = User(
            provider="google",
            provider_user_id=identity.sub,
            email=identity.email,
            email_verified=identity.email_verified,
            display_name=identity.name or (identity.email or "ThreadLoop user"),
            avatar_url=identity.picture,
        )
        db.add(user)
        db.flush()
    else:
        user = existing

    issued = issue_session(user, db=db, response=response, settings=settings)
    db.commit()
    db.refresh(user)

    return Session(
        link_required=False,
        access_token=issued.access_token,
        expires_at=issued.access_token_expires_at,
        user=UserOut.model_validate(user),
    )


# --- session lifecycle -------------------------------------------------------


def _clear_refresh_cookie(response: Response, *, settings: Settings) -> None:
    """Best-effort cookie clear with attributes matching `set_refresh_cookie`.

    Browsers only clear a cookie when the unset call's attributes match the
    cookie's original `Path`/`Domain`/`Secure`/`SameSite` — anything else
    leaves a stranded cookie. Mirroring `set_refresh_cookie` keeps logout
    actually effective in real browsers.
    """
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path="/",
        domain=settings.refresh_cookie_domain,
        secure=settings.refresh_cookie_secure,
        samesite=cast(Literal["lax", "strict", "none"], settings.refresh_cookie_samesite),
        httponly=True,
    )


def _refresh_failure_response(
    *, code: str, message: str, settings: Settings, clear_cookie: bool
) -> JSONResponse:
    """Build a 401 JSONResponse for the refresh route, optionally clearing the
    refresh cookie on the way out.

    Why JSONResponse rather than `raise HTTPException`: raising the exception
    short-circuits FastAPI's response-building, which means any cookies we
    attached to the injected `Response` get discarded by the exception
    handler. Returning a concrete response keeps the `Set-Cookie` clear
    actually visible to the client.
    """
    payload = JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": {"code": code, "message": message}},
    )
    if clear_cookie:
        _clear_refresh_cookie(payload, settings=settings)
    return payload


_INVALID_REFRESH_MESSAGE = (
    "Refresh token is missing, expired, revoked, or has been reused."
)


@router.post(
    "/refresh",
    summary="Issue a new access token using the refresh cookie",
    responses={
        200: {"model": Session},
        401: {"description": "Refresh token missing / invalid / revoked / reused."},
    },
)
def refresh_session(
    request: Request,
    db: DbSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    """Rotate the refresh token and mint a fresh access JWT.

    Failure mapping (all → 401, single envelope, no detail leaked):
      - cookie missing
      - hash doesn't match any row (forged / stale)
      - row is expired
      - row is revoked → **reuse-detection path**: revoke ALL of that user's
        refresh tokens (likely token theft per RFC 0001 § Failure modes) and
        return 401. The client must restart the SSO flow.

    Success path:
      - revoke the incoming row's `revoked_at`
      - mint a fresh refresh token (new row, rewritten cookie)
      - mint a new access JWT
      - commit

    Returns `JSONResponse` directly rather than via `raise HTTPException`
    so the `Set-Cookie` clear / set headers actually reach the client —
    raised exceptions discard the injected response object's headers.
    """
    cookie_value = request.cookies.get(settings.refresh_cookie_name)
    if not cookie_value:
        # No cookie to clear, no reason to attach Set-Cookie.
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "detail": {
                    "code": "no_refresh_token",
                    "message": "Missing refresh cookie.",
                }
            },
        )

    incoming_hash = hash_refresh_token(cookie_value, hmac_key=settings.refresh_token_hmac_key)
    row = db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == incoming_hash)
    ).scalar_one_or_none()

    if row is None:
        # Hash didn't match anything we issued. Could be stale / forged.
        # Clear the bad cookie so the client doesn't keep replaying it.
        return _refresh_failure_response(
            code="invalid_refresh_token",
            message=_INVALID_REFRESH_MESSAGE,
            settings=settings,
            clear_cookie=True,
        )

    now = datetime.now(UTC)

    if row.is_revoked():
        # Reuse detection — RFC 0001 § Failure modes. The current token's row
        # exists but was already rotated. Either a legitimate replay (e.g.
        # browser back-button) or active token theft; we can't tell, so we
        # treat it as theft and burn the user's whole refresh-token surface.
        db.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == row.user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        db.commit()
        logger.warning(
            "Refresh-token reuse detected for user_id=%s; revoked all tokens",
            row.user_id,
        )
        return _refresh_failure_response(
            code="invalid_refresh_token",
            message=_INVALID_REFRESH_MESSAGE,
            settings=settings,
            clear_cookie=True,
        )

    if row.is_expired(now):
        # Past `expires_at`. Don't bother revoking — already inert. Still
        # clear the client cookie so future requests don't carry the
        # graveyard token.
        return _refresh_failure_response(
            code="invalid_refresh_token",
            message=_INVALID_REFRESH_MESSAGE,
            settings=settings,
            clear_cookie=True,
        )

    # Happy path — valid, active row. Rotate.
    user = db.get(User, row.user_id)
    if user is None:
        # User deleted between issuance and now. Shouldn't normally happen
        # (CASCADE on user delete clears their tokens), but defend against
        # races. Same 401 envelope; nothing to leak.
        return _refresh_failure_response(
            code="invalid_refresh_token",
            message=_INVALID_REFRESH_MESSAGE,
            settings=settings,
            clear_cookie=True,
        )

    row.revoke(now)
    new_plaintext, _new_row = mint_refresh_token(user, db=db, settings=settings, now=now)
    access_token, access_expires_at = mint_access_token(user, settings=settings, now=now)
    db.commit()
    db.refresh(user)

    success = Session(
        link_required=False,
        access_token=access_token,
        expires_at=access_expires_at,
        user=UserOut.model_validate(user),
    )
    response = JSONResponse(
        status_code=status.HTTP_200_OK,
        content=success.model_dump(mode="json", exclude_none=True),
    )
    set_refresh_cookie(response, new_plaintext, settings=settings)
    return response


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the current refresh token and clear the cookie",
    response_class=Response,
)
def logout(
    request: Request,
    db: DbSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Revoke the refresh token associated with the current cookie. Idempotent.

    Always returns 204 — a missing cookie, a hash that doesn't match any row,
    or a row that's already revoked are all "the user is logged out" from the
    server's perspective. We unconditionally clear the cookie attribute on
    the returned response so subsequent requests stop carrying it.

    Constructs the response directly rather than mutating the `Depends`-
    injected one — the latter is not the response that actually ships to
    the client when the route returns its own `Response`.
    """
    cookie_value = request.cookies.get(settings.refresh_cookie_name)
    if cookie_value:
        incoming_hash = hash_refresh_token(cookie_value, hmac_key=settings.refresh_token_hmac_key)
        row = db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == incoming_hash)
        ).scalar_one_or_none()
        if row is not None and not row.is_revoked():
            row.revoke()
            db.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(response, settings=settings)
    return response


def _handle_facebook_callback(
    *,
    body: FacebookSsoCallbackInput,
    response: Response,
    db: DbSession,
    settings: Settings,
) -> Session:
    """Facebook SSO callback.

    Differs from Google / Apple in two important ways:

    1. **No JWT to verify.** The client hands us an opaque user access token;
       we resolve identity via two Graph API calls (`/debug_token` then
       `/me`). The Graph API is the trust anchor — there is no JWKS, no key
       rotation, no in-process cache. See `app.auth.facebook` for the flow.

    2. **No verified email.** Facebook does not expose `email_verified` in
       the Graph response, so the verifier hard-codes `email_verified=False`
       on every Facebook identity. The cross-provider collision check below
       therefore never fires for Facebook — matching against an unverified
       email would be the same account-takeover vector the Google and Apple
       branches guard against. This is intentional, not an oversight; the
       absence of a `link_required` path on Facebook sign-ins is documented
       in `docs/auth.md` § Facebook specifics.
    """
    try:
        identity = verify_facebook_access_token(
            body.access_token,
            app_id=settings.facebook_app_id,
            app_secret=settings.facebook_app_secret,
        )
    except GraphApiUnavailableError:
        logger.warning("Facebook Graph API unavailable; returning 503")
        raise _http_error(
            "graph_api_unavailable",
            "Facebook Graph API is unreachable; please retry.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from None
    except InvalidFacebookTokenError as exc:
        # Don't echo the upstream message — it can carry token contents.
        logger.info("Facebook access token rejected: %s", exc)
        raise _http_error(
            "invalid_token",
            "Facebook access token is invalid or expired.",
            status.HTTP_401_UNAUTHORIZED,
        ) from None

    existing = db.execute(
        select(User).where(User.provider == "facebook", User.provider_user_id == identity.sub)
    ).scalar_one_or_none()

    # Cross-provider collision check. Structurally identical to the Google
    # branch, but `identity.email_verified` is hard-coded False by the
    # verifier (see module docstring), so this branch never fires in
    # practice. The conditional is kept verbatim rather than dead-coded out
    # so a future change to Facebook's Graph response (e.g. them adding
    # `verified` to /me) plugs in cleanly without requiring the route layer
    # to also be revised.
    if existing is None and identity.email and identity.email_verified:
        collision = db.execute(
            select(User).where(
                User.email == identity.email,
                User.email_verified.is_(True),
                User.provider != "facebook",
            )
        ).scalar_one_or_none()
        if collision is not None:
            link_token, _ = issue_link_token(
                existing_user_id=collision.id,
                new_provider="facebook",
                new_provider_user_id=identity.sub,
                new_email=identity.email,
                settings=settings,
            )
            return Session(
                link_required=True,
                link_provider=cast(AuthProvider, collision.provider),
                link_token=link_token,
            )

    if existing is None:
        user = User(
            provider="facebook",
            provider_user_id=identity.sub,
            email=identity.email,
            email_verified=identity.email_verified,
            display_name=identity.name or (identity.email or "ThreadLoop user"),
            avatar_url=identity.picture,
        )
        db.add(user)
        db.flush()
    else:
        user = existing

    issued = issue_session(user, db=db, response=response, settings=settings)
    db.commit()
    db.refresh(user)

    return Session(
        link_required=False,
        access_token=issued.access_token,
        expires_at=issued.access_token_expires_at,
        user=UserOut.model_validate(user),
    )


def _handle_apple_callback(
    *,
    body: AppleSsoCallbackInput,
    response: Response,
    db: DbSession,
    settings: Settings,
) -> Session:
    """Apple SSO callback.

    Apple-specific behaviour vs the Google branch:

    1. **Hide-My-Email relay bypass.** When Apple's ID token carries
       `is_private_email: true`, the `email` claim is a per-app relay address
       (`*@privaterelay.appleid.com`). Matching that against an existing
       different-provider user's `email` would never legitimately succeed —
       and worse, would let an attacker who created a relay address claim a
       random verified-email account. We skip the cross-provider collision
       check entirely on relay addresses and treat the sign-in as a fresh
       identity. (RFC 0001 § Account linking; covered by an explicit test.)

    2. **Name only on the first sign-in.** Apple includes `name` in its
       ID-token-equivalent payload only on the very first auth response of
       a session — and only when the app requested the `name` scope. The
       client surfaces it via the `name` body field. We use it to seed
       `display_name` on a freshly-created user; on subsequent sign-ins the
       existing row's `display_name` is reused untouched.

    3. **No `code` exchange in this PR.** The OpenAPI contract requires the
       `code` field for upstream parity with Apple's full OAuth flow (which
       returns an Apple-side refresh token), but the ID token alone is
       sufficient to establish identity. Exchanging the code at
       `appleid.apple.com/auth/token` would only matter if we wanted to
       mint Apple-side refresh tokens, which we don't — our refresh-token
       lifecycle lives in `refresh_tokens`. The `client_secret` JWT signing
       helpers in `app.auth.apple` are exposed for a future job (RFC §
       Risks defers scheduled rotation) without being on the hot path here.
    """
    try:
        identity = verify_apple_id_token(
            body.id_token,
            expected_audience=settings.apple_client_id,
        )
    except AppleJwksUnavailableError:
        logger.warning("Apple JWKS unavailable; returning 503")
        raise _http_error(
            "jwks_unavailable",
            "Apple JWKS endpoint is unreachable; please retry.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from None
    except InvalidAppleTokenError as exc:
        # Don't echo the upstream message — it can carry token contents.
        logger.info("Apple ID token rejected: %s", exc)
        raise _http_error(
            "invalid_token",
            "Apple ID token is invalid or expired.",
            status.HTTP_401_UNAUTHORIZED,
        ) from None

    existing = db.execute(
        select(User).where(User.provider == "apple", User.provider_user_id == identity.sub)
    ).scalar_one_or_none()

    # Cross-provider collision detection. Same shape as Google with two
    # Apple-specific guards:
    #   - skip when the incoming email is a relay (no real address to match)
    #   - skip when the email is unverified (covered by the same `email_verified`
    #     guard the Google branch uses)
    if (
        existing is None
        and identity.email
        and identity.email_verified
        and not identity.is_private_email
    ):
        collision = db.execute(
            select(User).where(
                User.email == identity.email,
                User.email_verified.is_(True),
                User.provider != "apple",
            )
        ).scalar_one_or_none()
        if collision is not None:
            link_token, _ = issue_link_token(
                existing_user_id=collision.id,
                new_provider="apple",
                new_provider_user_id=identity.sub,
                new_email=identity.email,
                settings=settings,
            )
            return Session(
                link_required=True,
                link_provider=cast(AuthProvider, collision.provider),
                link_token=link_token,
            )

    if existing is None:
        # `body.name` lands here ONLY on the first auth response of the
        # session per Apple's behaviour — never overwrite from a missing
        # name on subsequent sign-ins.
        display_name = body.name or identity.email or "ThreadLoop user"
        user = User(
            provider="apple",
            provider_user_id=identity.sub,
            email=identity.email,
            email_verified=identity.email_verified,
            display_name=display_name,
            avatar_url=None,
        )
        db.add(user)
        db.flush()
    else:
        user = existing

    issued = issue_session(user, db=db, response=response, settings=settings)
    db.commit()
    db.refresh(user)

    return Session(
        link_required=False,
        access_token=issued.access_token,
        expires_at=issued.access_token_expires_at,
        user=UserOut.model_validate(user),
    )
