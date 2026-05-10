"""Per-user provider identities — the source of truth for "this user can sign
in via these providers".

Background. Pre-#18, identity lived as `users.(provider, provider_user_id)`
— a denormalized 1:1 mapping that worked while one user could only hold one
identity. The slice-4 account-linking flow (#18) introduces the case where
one `users.id` row holds two `(provider, provider_user_id)` tuples — e.g. a
user signed up with Google, later linked Facebook. The denormalized columns
on `users` can no longer be the lookup index.

Resolution per the `[backend-dev pushback]` on #18: keep `users.provider` /
`users.provider_user_id` as "primary identity for this user" (no contract
bump on `User`), and add this table as the source of truth for "all
identities this user holds." Callback detection becomes a lookup against
`user_identities.(provider, provider_user_id)` rather than against the
denormalized `users` columns. The merged-account post-condition is:

    users.id          = X
    users.provider    = "google"            ← the original / primary
    users.provider_user_id = "google-sub-1"

    user_identities rows for X:
      ("google", "google-sub-1", linked_at = original signup time)
      ("facebook", "fb-sub-2",   linked_at = link-flow completion time)

Why both columns on `users` AND a `user_identities` row for the primary:

- The `users` columns stay populated so `User.provider` (the wire field)
  has a meaningful value; today's only consumer (`MePage.tsx`) keeps
  reading the scalar field unchanged.
- The `user_identities` row is what callbacks actually look up. The
  primary identity gets its row at user creation time so the lookup is
  uniform across "first sign-in" and "subsequent sign-in via a linked
  provider".

Schema is documented in `docs/auth.md` § Account linking.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class UserIdentity(Base):
    """One row per `(provider, provider_user_id)` a user can sign in with.

    The unique constraint on `(provider, provider_user_id)` prevents two
    distinct users from claiming the same provider identity — the
    invariant the legacy `users.uq_users_provider_sub` constraint enforced
    before this table existed. Cascade-on-user-delete mirrors
    `refresh_tokens` so a future GDPR-deletion flow drops the rows
    automatically.
    """

    __tablename__ = "user_identities"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_user_id",
            name="uq_user_identities_provider_sub",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_user_id: Mapped[str] = mapped_column(String(255), nullable=False)

    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
