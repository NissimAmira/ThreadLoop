"""User-facing routes.

Currently just `GET /api/me`, the only consumer of `require_user` until the
listings / transactions epics ship. Why a separate router from `auth.py`:
`/api/me` lives under `/api/me`, not `/api/auth/me`, and the auth router
carries a `require_auth_enabled` router-level dependency that would 404
this route under `AUTH_ENABLED=false` even though the OpenAPI contract
doesn't promise 404 for `/api/me`. Splitting routers keeps the contract
honest.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.auth.deps import require_user
from app.auth.schemas import UserOut
from app.models import User

router = APIRouter(tags=["users"])


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
