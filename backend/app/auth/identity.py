"""Identity resolution helpers — the post-#18 source of truth for
"which `users` row owns this `(provider, provider_user_id)`."

Pre-#18, callbacks looked identities up directly via
`users.(provider, provider_user_id)`. That worked while the relationship
was strictly 1:1 — one user, one provider identity. The slice-4
account-linking flow (#18) breaks that invariant: a single `users` row
can hold multiple identities (the original / primary plus any linked
ones). Lookups must therefore go through `user_identities`, the
many-to-one association table introduced by migration 0003.

`users.(provider, provider_user_id)` columns are kept populated and
represent the **primary** identity (the original provider the user
signed up with). The wire field `User.provider` continues to expose
that scalar — no contract bump on `User`. See
`app/models/user_identity.py` for the full data-model rationale and
`docs/auth.md` § Account linking for the user-visible semantics.

This module exists so neither the callbacks nor the link route have to
know whether the identity-storage strategy is "the legacy denormalized
columns" or "the new association table" — they just call
`find_user_by_identity` / `add_identity_to_user`.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import User, UserIdentity


def find_user_by_identity(db: DbSession, *, provider: str, provider_user_id: str) -> User | None:
    """Return the `User` row that holds `(provider, provider_user_id)` as
    one of its identities — primary or linked. `None` if no match.

    Replaces the pre-#18 lookup pattern
    `select(User).where(User.provider == ..., User.provider_user_id == ...)`
    in callbacks. Migration 0003's backfill ensures every existing user
    has a matching `user_identities` row for their primary identity, so
    behaviour is unchanged for first-time / single-provider users; the
    new path additionally returns the right user when signing in via a
    linked secondary provider.
    """
    return db.execute(
        select(User)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .where(
            UserIdentity.provider == provider,
            UserIdentity.provider_user_id == provider_user_id,
        )
    ).scalar_one_or_none()


def register_primary_identity(
    db: DbSession, *, user: User, provider: str, provider_user_id: str
) -> UserIdentity:
    """Create the `user_identities` row that mirrors a freshly-inserted
    user's primary `(provider, provider_user_id)`.

    Called by every callback right after `db.add(user); db.flush()` so the
    new user is immediately findable via `find_user_by_identity`. Without
    this, a second sign-in for the same `(provider, sub)` would miss the
    cache and try to insert a duplicate (rejected by the unique
    constraint, but with a 500 instead of an idempotent 200). Caller is
    responsible for committing.
    """
    row = UserIdentity(
        user_id=user.id,
        provider=provider,
        provider_user_id=provider_user_id,
    )
    db.add(row)
    db.flush()
    return row


def link_identity_to_user(
    db: DbSession,
    *,
    user: User,
    provider: str,
    provider_user_id: str,
) -> UserIdentity:
    """Add a non-primary `(provider, provider_user_id)` identity to an
    existing user. Used by the slice-4 link route once the link token has
    been validated and the original-provider re-auth has succeeded.

    Does NOT touch `users.provider` / `users.provider_user_id` — the
    primary identity (and hence `User.provider` on the wire) is preserved
    across linking, matching the "primary" semantic documented in
    `docs/auth.md` § Account linking.

    Caller is responsible for committing. The route uses
    `db.flush()` here and lets the unit-of-work commit happen alongside
    the session-issuance side effects.
    """
    row = UserIdentity(
        user_id=user.id,
        provider=provider,
        provider_user_id=provider_user_id,
    )
    db.add(row)
    db.flush()
    return row
