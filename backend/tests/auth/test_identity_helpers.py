"""Unit tests for `app.auth.identity` — the user/identity lookup helpers
used by every callback after the slice-4 refactor.

The helpers are thin wrappers around the SQLAlchemy session, but two
behaviours are load-bearing enough to lock in:

1. `find_user_by_identity` returns `None` for unknown `(provider, sub)`
   (callbacks rely on this to drive the "create new user" branch).
2. `register_primary_identity` and `link_identity_to_user` both insert
   `user_identities` rows with the right `(user_id, provider,
   provider_user_id)` tuple — and the unique constraint surfaces a
   conflict on re-insert (so concurrent linking can't quietly create
   two rows for the same identity).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from alembic import command
from app.auth.identity import (
    find_user_by_identity,
    link_identity_to_user,
    register_primary_identity,
)
from app.models import User

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def _alembic_config(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
    return cfg


@pytest.fixture
def db_session(pg_url: str) -> Iterator[DbSession]:
    cfg = _alembic_config(pg_url)
    command.upgrade(cfg, "head")

    engine = create_engine(pg_url, future=True)
    with engine.begin() as conn:
        for table in (
            "consumed_link_tokens",
            "refresh_tokens",
            "user_identities",
            "users",
        ):
            conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))

    test_session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = test_session_local()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _make_user(db: DbSession, *, provider: str, sub: str) -> User:
    user = User(
        provider=provider,
        provider_user_id=sub,
        email=f"{sub}@example.com",
        email_verified=True,
        display_name=f"User {sub}",
        avatar_url=None,
    )
    db.add(user)
    db.flush()
    return user


def test_find_user_by_identity_returns_none_for_unknown_identity(
    db_session: DbSession,
) -> None:
    assert (
        find_user_by_identity(db_session, provider="google", provider_user_id="never-existed")
        is None
    )


def test_register_primary_identity_makes_user_findable(
    db_session: DbSession,
) -> None:
    """The whole point of the helper: after `User` row + primary-identity
    row are inserted, `find_user_by_identity` returns that user."""
    user = _make_user(db_session, provider="google", sub="google-primary-1")
    register_primary_identity(
        db_session, user=user, provider="google", provider_user_id="google-primary-1"
    )

    found = find_user_by_identity(
        db_session, provider="google", provider_user_id="google-primary-1"
    )
    assert found is not None
    assert found.id == user.id


def test_link_identity_to_user_makes_user_findable_via_secondary(
    db_session: DbSession,
) -> None:
    """After linking, signing in with the secondary provider must resolve
    to the same user — that's the whole purpose of the link route."""
    user = _make_user(db_session, provider="google", sub="google-primary-2")
    register_primary_identity(
        db_session, user=user, provider="google", provider_user_id="google-primary-2"
    )
    link_identity_to_user(
        db_session, user=user, provider="apple", provider_user_id="apple-linked-2"
    )

    found_via_primary = find_user_by_identity(
        db_session, provider="google", provider_user_id="google-primary-2"
    )
    found_via_secondary = find_user_by_identity(
        db_session, provider="apple", provider_user_id="apple-linked-2"
    )
    assert found_via_primary is not None
    assert found_via_secondary is not None
    assert found_via_primary.id == user.id == found_via_secondary.id


def test_link_identity_to_user_does_not_touch_users_columns(
    db_session: DbSession,
) -> None:
    """`User.provider` / `User.provider_user_id` represent the PRIMARY
    identity and must be preserved across linking — that's the contract
    `User.provider` (singular) on the wire depends on per the PR's
    `[backend-dev pushback]` resolution.
    """
    user = _make_user(db_session, provider="google", sub="google-primary-3")
    register_primary_identity(
        db_session, user=user, provider="google", provider_user_id="google-primary-3"
    )

    original_provider = user.provider
    original_sub = user.provider_user_id

    link_identity_to_user(
        db_session, user=user, provider="apple", provider_user_id="apple-linked-3"
    )

    db_session.refresh(user)
    assert user.provider == original_provider
    assert user.provider_user_id == original_sub


def test_uniqueness_on_provider_sub_across_users(db_session: DbSession) -> None:
    """The `(provider, provider_user_id)` unique constraint on
    `user_identities` is what stops two users from claiming the same
    provider identity. If a future change drops this constraint, the
    link route's idempotency story falls apart.
    """
    user_a = _make_user(db_session, provider="google", sub="google-a-uniq")
    register_primary_identity(
        db_session, user=user_a, provider="google", provider_user_id="google-a-uniq"
    )

    user_b = _make_user(db_session, provider="apple", sub="apple-b-uniq")
    register_primary_identity(
        db_session, user=user_b, provider="apple", provider_user_id="apple-b-uniq"
    )

    # Try to link the same `(google, google-a-uniq)` identity onto user_b.
    # The helper calls `db.flush()` internally, which is when SQLAlchemy
    # actually issues the INSERT — IntegrityError surfaces there, before
    # control returns to the caller. Either failure point is fine for the
    # invariant; we just need to confirm the duplicate is rejected by the
    # constraint, not silently allowed.
    with pytest.raises(IntegrityError):
        link_identity_to_user(
            db_session,
            user=user_b,
            provider="google",
            provider_user_id="google-a-uniq",
        )
    db_session.rollback()


def test_consumed_link_token_pk_uniqueness(db_session: DbSession) -> None:
    """Defense in depth: even without the route's pre-check on
    `consumed_link_tokens`, the PK must reject a duplicate `jti` insert.
    The route's TOCTOU window between "look up jti, insert jti" is
    fundamentally backed by this constraint.
    """
    from app.models import ConsumedLinkToken

    jti = uuid.uuid4().hex
    expires_at = datetime.now(UTC)

    db_session.add(ConsumedLinkToken(jti=jti, expires_at=expires_at))
    db_session.commit()

    db_session.add(ConsumedLinkToken(jti=jti, expires_at=expires_at))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
