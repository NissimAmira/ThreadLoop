"""Pydantic v2 wire schemas for the auth callbacks.

These mirror `shared/openapi.yaml` § auth and `shared/src/types/auth.ts` /
`shared/src/types/user.ts`. Keep field names + nullability in sync if either
side moves.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AuthProvider = Literal["google", "apple", "facebook"]


class GoogleSsoCallbackInput(BaseModel):
    """Body for `POST /api/auth/google/callback`."""

    id_token: str = Field(..., min_length=1, description="Google-issued ID token.")


class UserOut(BaseModel):
    """OpenAPI `User`. Field names match the spec verbatim."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: AuthProvider
    email: str | None
    email_verified: bool
    display_name: str
    avatar_url: str | None
    can_sell: bool
    can_purchase: bool
    seller_rating: float | None = None
    created_at: datetime
    updated_at: datetime


class Session(BaseModel):
    """OpenAPI `Session`. The same envelope serves the happy path and the
    pending-link path; client distinguishes via `link_required`. We populate
    only the fields appropriate for each branch and let Pydantic's
    `exclude_none` semantics keep the payload tight.
    """

    model_config = ConfigDict(from_attributes=True)

    link_required: bool = False
    access_token: str | None = None
    expires_at: datetime | None = None
    user: UserOut | None = None
    link_provider: AuthProvider | None = None
    link_token: str | None = None
