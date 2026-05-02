"""User-facing routes.

Currently just `GET /api/me`, the only consumer of `require_user` until the
listings / transactions epics ship.

Why a separate router from `auth.py`: `/api/me` lives under `/api/me`, not
`/api/auth/me`, so it can't share the auth router's `prefix="/auth"`. The
RFC-0001 `AUTH_ENABLED` rollout flag still applies though — when the flag
is off, the entire auth subsystem (including identity look-up) must look
like it doesn't exist. We attach the same `require_auth_enabled` dep at
the router level here so `/api/me` returns 404 in lockstep with the auth
routes. Without this, `/api/me` would be gated only by accident: a future
refactor that splits the dep graph differently, or a prod rollback that
flips `AUTH_ENABLED=false`, would otherwise leave `/api/me` validating
any 15-min-old bearer JWT signed with our key. Belt-and-braces — explicit
gate matches the rest of the auth surface.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.auth.deps import require_auth_enabled, require_user
from app.auth.schemas import UserOut
from app.models import User

router = APIRouter(
    tags=["users"],
    dependencies=[Depends(require_auth_enabled)],
)


@router.get(
    "/me",
    response_model=UserOut,
    summary="Get the currently authenticated user",
)
def get_me(user: Annotated[User, Depends(require_user)]) -> UserOut:
    """Return the authenticated user.

    `require_user` does all the heavy lifting: bearer-token extraction,
    signature/expiry/typ validation, and user lookup. A 401 from any of
    those failure modes shapes the OpenAPI `Error` envelope.
    """
    return UserOut.model_validate(user)
