"""Auth callback dispatcher.

Wire format follows `POST /api/auth/{provider}/callback` in
`shared/openapi.yaml`. This task (#14) implements the `google` branch only —
`apple` (#15) and `facebook` (#16) plug into the same dispatcher.

The whole router is gated behind `Settings.auth_enabled` per RFC 0001 §
Rollout plan step 1: every route returns 404 while the flag is off so we can
land the implementation environment-by-environment.

The 'find-or-create user' + 'detect cross-provider email collision' logic
lives here; everything cryptographic is in `app.auth.google` (token verify),
`app.auth.session` (mint + cookie), and `app.auth.link` (pending-link token).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Response, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.auth.google import (
    InvalidGoogleTokenError,
    JwksUnavailableError,
    verify_google_id_token,
)
from app.auth.link import issue_link_token
from app.auth.schemas import AuthProvider, GoogleSsoCallbackInput, Session, UserOut
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

    if provider != "google":
        # #15 (apple) and #16 (facebook) will replace this with a real impl.
        # 404 with a stable `code` is the right semantic per the OpenAPI
        # contract (200/400/401/404/503 for this path) — 501 would be drift.
        # Once those PRs land they replace this branch with a real handler.
        raise _http_error(
            "provider_not_implemented",
            f"Provider {provider!r} is not yet implemented.",
            status.HTTP_404_NOT_FOUND,
        )

    try:
        google_body = GoogleSsoCallbackInput.model_validate(body)
    except ValidationError as exc:
        # Surface as 422 (FastAPI's default for body validation), matching
        # the behaviour FastAPI would have produced if we'd typed `body` as
        # `GoogleSsoCallbackInput` directly.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(),
        ) from None

    return _handle_google_callback(body=google_body, response=response, db=db, settings=settings)


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
