"""Integration tests that exercise the actual Alembic migration chain against
a real Postgres container. These lock in two invariants that the unit tests
in `test_refresh_token_model.py` cannot:

1. The migration round-trips cleanly (`upgrade head` -> `downgrade -1` -> `upgrade head`).
2. Deleting a `users` row cascades to `refresh_tokens` rows, per the FK declaration.

Both are claimed in PR #29's body but were not previously verified in CI.
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
    """Upgrade head, downgrade -1, upgrade head again. At each step verify
    `refresh_tokens` exists or is gone as expected.

    This catches the case where someone edits the model and forgets a
    follow-up migration, or breaks `downgrade()` for the refresh_tokens
    revision."""
    cfg = _alembic_config(pg_url)
    engine = create_engine(pg_url)

    command.upgrade(cfg, "head")
    inspector = inspect(engine)
    assert "refresh_tokens" in inspector.get_table_names()

    command.downgrade(cfg, "-1")
    inspector = inspect(engine)
    assert "refresh_tokens" not in inspector.get_table_names()
    assert "users" in inspector.get_table_names(), (
        "downgrade -1 should drop only refresh_tokens, not the prior schema"
    )

    command.upgrade(cfg, "head")
    inspector = inspect(engine)
    assert "refresh_tokens" in inspector.get_table_names()

    engine.dispose()


def test_user_delete_cascades_refresh_tokens(pg_url: str) -> None:
    """Insert a user + a refresh_token row, delete the user, expect the
    refresh_token row to be cascade-deleted by the FK declaration.
    """
    cfg = _alembic_config(pg_url)
    engine = create_engine(pg_url)

    command.upgrade(cfg, "head")

    user_id = uuid.uuid4()
    token_id = uuid.uuid4()
    now = datetime.now(UTC)

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users "
                "(id, provider, provider_user_id, display_name, "
                "email_verified, can_sell, can_purchase) "
                "VALUES (:id, 'google', :sub, 'Test User', true, false, true)"
            ),
            {"id": user_id, "sub": f"sub-{user_id}"},
        )
        conn.execute(
            text(
                "INSERT INTO refresh_tokens "
                "(id, user_id, token_hash, issued_at, expires_at) "
                "VALUES (:id, :uid, :hash, :issued, :exp)"
            ),
            {
                "id": token_id,
                "uid": user_id,
                "hash": b"\x42" * 32,
                "issued": now,
                "exp": now + timedelta(days=30),
            },
        )

    with engine.begin() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM refresh_tokens WHERE id = :id"),
            {"id": token_id},
        ).scalar_one()
        assert count == 1, "refresh_token should exist before user delete"

        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})

        count = conn.execute(
            text("SELECT count(*) FROM refresh_tokens WHERE id = :id"),
            {"id": token_id},
        ).scalar_one()
        assert count == 0, "refresh_token should be cascade-deleted with user"

    engine.dispose()
