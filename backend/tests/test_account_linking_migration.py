"""Integration tests for migration 0003 — `user_identities` and
`consumed_link_tokens` tables introduced for the slice-4 account-linking
flow (#18).

Locks in three invariants the unit tests can't:

1. The migration round-trips (`upgrade head` -> `downgrade -1` -> `upgrade head`).
2. Existing users get a backfill row in `user_identities` so callbacks
   that look up via `(provider, provider_user_id)` keep working without
   a flag-day.
3. Deleting a `users` row cascades to `user_identities` rows
   (`consumed_link_tokens` has no FK so it's unaffected — by design,
   per the model docstring).
"""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def _alembic_config(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
    return cfg


def test_migration_round_trip(pg_url: str) -> None:
    """0001 → 0002 → 0003 → downgrade -1 → 0003 again. The new tables
    appear and disappear in lockstep. Non-trivial because Alembic gets
    indexes and FKs subtly wrong if `downgrade()` doesn't mirror
    `upgrade()` exactly.
    """
    cfg = _alembic_config(pg_url)
    engine = create_engine(pg_url)

    command.upgrade(cfg, "head")
    inspector = inspect(engine)
    assert "user_identities" in inspector.get_table_names()
    assert "consumed_link_tokens" in inspector.get_table_names()

    command.downgrade(cfg, "-1")
    inspector = inspect(engine)
    assert "user_identities" not in inspector.get_table_names()
    assert "consumed_link_tokens" not in inspector.get_table_names()
    # Pre-#18 tables must still be there — downgrade must not over-shoot.
    assert "users" in inspector.get_table_names()
    assert "refresh_tokens" in inspector.get_table_names()

    command.upgrade(cfg, "head")
    inspector = inspect(engine)
    assert "user_identities" in inspector.get_table_names()
    assert "consumed_link_tokens" in inspector.get_table_names()

    engine.dispose()


def test_backfill_creates_user_identity_for_existing_users(pg_url: str) -> None:
    """A user inserted at 0002 must get a `user_identities` row when 0003
    runs. Without this backfill, the `find_user_by_identity` lookup in the
    refactored callbacks would return None for pre-#18 users on their
    next sign-in, triggering a duplicate insert (rejected by the
    `users` unique constraint, surfaced as a 500).
    """
    cfg = _alembic_config(pg_url)
    engine = create_engine(pg_url)

    # Roll back to 0002 (pre-#18 schema).
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0002")

    # Plant a pre-#18 user.
    user_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users "
                "(id, provider, provider_user_id, display_name, "
                "email_verified, can_sell, can_purchase) "
                "VALUES (:id, 'google', :sub, 'Pre-#18 User', true, false, true)"
            ),
            {"id": user_id, "sub": f"sub-pre-{user_id.hex[:8]}"},
        )

    # Apply 0003 — backfill must run.
    command.upgrade(cfg, "head")

    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT provider, provider_user_id FROM user_identities WHERE user_id = :uid"),
            {"uid": user_id},
        ).all()
        assert len(rows) == 1, "backfill must create one identity row per existing user"
        assert rows[0][0] == "google"
        assert rows[0][1] == f"sub-pre-{user_id.hex[:8]}"

    engine.dispose()


def test_user_delete_cascades_user_identities(pg_url: str) -> None:
    """Cascade-on-user-delete mirrors `refresh_tokens` so a future
    GDPR-deletion flow drops linked-identity rows automatically without
    a separate sweep step.
    """
    cfg = _alembic_config(pg_url)
    engine = create_engine(pg_url)

    command.upgrade(cfg, "head")

    user_id = uuid.uuid4()
    primary_sub = f"sub-cascade-{user_id.hex[:8]}"
    linked_sub = f"apple-cascade-{user_id.hex[:8]}"
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users "
                "(id, provider, provider_user_id, display_name, "
                "email_verified, can_sell, can_purchase) "
                "VALUES (:id, 'google', :sub, 'Cascade Test User', true, false, true)"
            ),
            {"id": user_id, "sub": primary_sub},
        )
        # The backfill in 0003 only fires for users present BEFORE the
        # migration ran — this test inserts a user post-migration, so we
        # must insert both identity rows manually (matching what the
        # callback layer does at runtime via `register_primary_identity`
        # and `link_identity_to_user`).
        conn.execute(
            text(
                "INSERT INTO user_identities "
                "(id, user_id, provider, provider_user_id) "
                "VALUES (gen_random_uuid(), :uid, 'google', :sub)"
            ),
            {"uid": user_id, "sub": primary_sub},
        )
        conn.execute(
            text(
                "INSERT INTO user_identities "
                "(id, user_id, provider, provider_user_id) "
                "VALUES (gen_random_uuid(), :uid, 'apple', :sub2)"
            ),
            {"uid": user_id, "sub2": linked_sub},
        )

    with engine.begin() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM user_identities WHERE user_id = :uid"),
            {"uid": user_id},
        ).scalar_one()
        assert count == 2, "expected primary + linked identity rows before delete"

        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})

        count = conn.execute(
            text("SELECT count(*) FROM user_identities WHERE user_id = :uid"),
            {"uid": user_id},
        ).scalar_one()
        assert count == 0, "user_identities rows should cascade with user delete"

    engine.dispose()


def test_consumed_link_tokens_has_no_user_fk(pg_url: str) -> None:
    """`consumed_link_tokens` is intentionally user-independent — the
    `jti` is opaque to user identity and we don't want the table size
    bound to user-deletion patterns. Lock in the absence of a FK so a
    future "let's also FK to users for tidiness" PR is forced to consider
    the consequence.
    """
    cfg = _alembic_config(pg_url)
    engine = create_engine(pg_url)
    command.upgrade(cfg, "head")

    inspector = inspect(engine)
    fks = inspector.get_foreign_keys("consumed_link_tokens")
    assert fks == [], (
        "consumed_link_tokens must have no foreign keys; "
        "decoupling jti from user identity is intentional"
    )

    # Insert + read round-trip.
    now = datetime.now(UTC)
    jti = uuid.uuid4().hex
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO consumed_link_tokens (jti, expires_at) VALUES (:jti, :exp)"),
            {"jti": jti, "exp": now + timedelta(minutes=10)},
        )
        row = conn.execute(
            text("SELECT jti FROM consumed_link_tokens WHERE jti = :jti"),
            {"jti": jti},
        ).scalar_one()
    engine.dispose()
    assert row == jti
