"""Pydantic v2 wire schemas for the auth callbacks.

These mirror `shared/openapi.yaml` § auth and `shared/src/types/auth.ts` /
`shared/src/types/user.ts`. Keep field names + nullability in sync if either
side moves.

Wire-shape policy (ADR 0009): every model that crosses the HTTP boundary —
request bodies and response shapes — uses `to_camel` aliases so the JSON on
the wire is camelCase while Python attribute names stay snake_case. Both
`populate_by_name=True` (so internal callers still work via the Python
attribute name) and `serialize_by_alias=True` (so FastAPI uses the alias on
the way out — without this, only inbound parsing accepts the alias) are
required. The `WireBase` mixin centralises that config so a future schema
can't accidentally ship without it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

AuthProvider = Literal["google", "apple", "facebook"]


class WireBase(BaseModel):
    """Mixin: every wire model camelCases its JSON aliases per ADR 0009."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class GoogleSsoCallbackInput(WireBase):
    """Body for `POST /api/auth/google/callback`."""

    id_token: str = Field(..., min_length=1, description="Google-issued ID token.")


class AppleSsoCallbackInput(WireBase):
    """Body for `POST /api/auth/apple/callback`.

    Apple sends `name` only in the FIRST callback of a session (and even then
    only when the app requested the `name` scope). Subsequent sign-ins omit
    it entirely. We treat it as best-effort: if present, it seeds the new
    user's `display_name`; if absent, the route falls back to email then to
    a literal default. `code` is required by the OpenAPI contract for
    upstream parity, but #15 itself doesn't exchange it — the ID token alone
    establishes identity. (Documented in `docs/auth.md` § Apple specifics.)
    """

    id_token: str = Field(..., min_length=1, description="Apple-issued ID token.")
    code: str = Field(..., min_length=1, description="Apple authorization code.")
    name: str | None = Field(
        default=None,
        description=(
            "User's name as Apple's JS SDK / native flow surfaces it on the "
            "first sign-in only. Best-effort; absent on subsequent sign-ins."
        ),
    )


class FacebookSsoCallbackInput(WireBase):
    """Body for `POST /api/auth/facebook/callback`.

    Facebook is the odd one out: there is no ID token to verify against a
    JWKS — the client hands us a user access token, and the backend resolves
    it server-side via the Graph API (`/debug_token` to validate the token
    was issued for our app, then `/me` to read the profile). See
    `docs/auth.md` § Facebook specifics.
    """

    access_token: str = Field(
        ...,
        min_length=1,
        description="Facebook user access token (resolved against Graph API server-side).",
    )


class UserOut(WireBase):
    """OpenAPI `User`. Field names match the spec verbatim."""

    # `from_attributes=True` is layered on top of WireBase's config so we can
    # build instances directly from SQLAlchemy rows via `model_validate(row)`
    # without losing the camelCase wire aliasing.
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        from_attributes=True,
    )

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


class Session(WireBase):
    """OpenAPI `Session`. The same envelope serves the happy path and the
    pending-link path; client distinguishes via `link_required`. We populate
    only the fields appropriate for each branch and let Pydantic's
    `exclude_none` semantics keep the payload tight.

    Field optionality is enforced via `_check_branch_invariants` rather than
    relying on caller discipline — a misrouted field would be silently
    serialized today, and #18 / #17 are about to add more callers.
    """

    link_required: bool = False
    access_token: str | None = None
    expires_at: datetime | None = None
    user: UserOut | None = None
    link_provider: AuthProvider | None = None
    link_token: str | None = None

    @model_validator(mode="after")
    def _check_branch_invariants(self) -> Session:
        """Enforce the discriminated-union shape implied by `link_required`.

        - `link_required=True` → `link_provider` + `link_token` set;
          `access_token` / `expires_at` / `user` must all be None.
        - `link_required=False` → `access_token` + `expires_at` + `user` set;
          `link_provider` / `link_token` must be None.

        Validators run on Python attribute names, NOT aliases — `WireBase`'s
        `alias_generator` only changes wire serialization. Don't rename the
        attributes here in a misguided attempt to align them with the alias.

        Raises `ValueError` so Pydantic surfaces it as a normal validation
        error.
        """
        if self.link_required:
            if self.link_provider is None or self.link_token is None:
                raise ValueError("link_required=True requires link_provider and link_token")
            if (
                self.access_token is not None
                or self.expires_at is not None
                or self.user is not None
            ):
                raise ValueError(
                    "link_required=True must not carry access_token / expires_at / user"
                )
        else:
            if self.access_token is None or self.expires_at is None or self.user is None:
                raise ValueError("link_required=False requires access_token, expires_at, and user")
            if self.link_provider is not None or self.link_token is not None:
                raise ValueError("link_required=False must not carry link_provider or link_token")
        return self
