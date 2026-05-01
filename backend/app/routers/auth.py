"""Auth callback dispatcher.

Wire format follows `POST /api/auth/{provider}/callback` in
`shared/openapi.yaml`. The `google` branch shipped in #14, the `apple` branch
shipped in #15, and the `facebook` branch shipped in #16 (this PR). All three
plug into the same dispatcher.

The whole router is gated behind `Settings.auth_enabled` per RFC 0001 §
Rollout plan step 1: every route returns 404 while the flag is off so we can
land the implementation environment-by-environment.

The 'find-or-create user' + 'detect cross-provider email collision' logic
lives here; everything cryptographic / network-bound is in `app.auth.google`
/ `app.auth.apple` / `app.auth.facebook` (token verify), `app.auth.session`
(mint + cookie), and `app.auth.link` (pending-link token).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Response, status
from pydantic import ValidationError
from sqlalchemy import select
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
from app.auth.session import issue_session
from app.config import Settings, get_settings
from app.db import get_db
from app.models import User

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
