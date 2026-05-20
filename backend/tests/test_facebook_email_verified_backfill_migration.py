"""Integration test for migration 0004 — the #70 data backfill that
flips `email_verified` to true for pre-#69 Facebook-primary `users`
rows that carry a non-null email.

Locks in the invariant the migration body claims: `upgrade()` touches
*only* the target rows. Specifically:

- A pre-fix Facebook row with a non-null email IS flipped.
- A Facebook row with `email IS NULL` (user declined the `email` scope)
  is NOT touched — there is nothing to verify.
- A Google row with `email_verified=false` is NOT touched — the backfill
  is provider-scoped to Facebook.
- An Apple row with `email_verified=false` is NOT touched — same reason.

Run order: roll back to 0003 (pre-#70 schema), plant rows, apply 0004,
assert. Marked `integration` because it exercises the real Alembic chain
against a Postgres container.
"""

import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def _alembic_config(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
    return cfg


def _insert_user(
    conn,
    *,
    user_id: uuid.UUID,
    provider: str,
    sub: str,
    email: str | None,
    email_verified: bool,
) -> None:
    conn.execute(
        text(
            "INSERT INTO users "
            "(id, provider, provider_user_id, email, display_name, "
            "email_verified, can_sell, can_purchase) "
            "VALUES (:id, :provider, :sub, :email, :name, "
            ":verified, false, true)"
        ),
        {
            "id": user_id,
            "provider": provider,
            "sub": sub,
            "email": email,
            "name": f"{provider} test user",
            "verified": email_verified,
        },
    )


def _verified(conn, user_id: uuid.UUID) -> bool:
    return conn.execute(
        text("SELECT email_verified FROM users WHERE id = :id"),
        {"id": user_id},
    ).scalar_one()


def test_backfill_flips_only_pre_fix_facebook_rows_with_email(pg_url: str) -> None:
    """0004 must flip `email_verified` for pre-#69 Facebook-primary rows
    that have a non-null email — and leave every other row untouched.

    Non-trivial because the backfill is a blunt `UPDATE`; an over-broad
    predicate would wrongly verify Google/Apple rows or Facebook rows
    that declined the email scope, and an under-broad one would miss the
    rows the #70 fix exists to repair.
    """
    cfg = _alembic_config(pg_url)
    engine = create_engine(pg_url)

    # Roll back to 0003 — the pre-#70 schema. `email_verified` already
    # exists (added in 0001); 0004 is a pure data migration.
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0003")

    # Target: a pre-fix Facebook row with a non-null email — MUST flip.
    fb_with_email = uuid.uuid4()
    # Facebook row with no email (user declined the scope) — MUST stay.
    fb_no_email = uuid.uuid4()
    # Google row, unverified — MUST stay (provider-scoped to Facebook).
    google_unverified = uuid.uuid4()
    # Apple row, unverified — MUST stay.
    apple_unverified = uuid.uuid4()
    # A Facebook row already verified — MUST stay true (idempotency).
    fb_already_verified = uuid.uuid4()

    with engine.begin() as conn:
        _insert_user(
            conn,
            user_id=fb_with_email,
            provider="facebook",
            sub=f"fb-email-{fb_with_email.hex[:8]}",
            email="prefix-fb@example.com",
            email_verified=False,
        )
        _insert_user(
            conn,
            user_id=fb_no_email,
            provider="facebook",
            sub=f"fb-noemail-{fb_no_email.hex[:8]}",
            email=None,
            email_verified=False,
        )
        _insert_user(
            conn,
            user_id=google_unverified,
            provider="google",
            sub=f"gg-{google_unverified.hex[:8]}",
            email="google@example.com",
            email_verified=False,
        )
        _insert_user(
            conn,
            user_id=apple_unverified,
            provider="apple",
            sub=f"ap-{apple_unverified.hex[:8]}",
            email="apple@example.com",
            email_verified=False,
        )
        _insert_user(
            conn,
            user_id=fb_already_verified,
            provider="facebook",
            sub=f"fb-verified-{fb_already_verified.hex[:8]}",
            email="postfix-fb@example.com",
            email_verified=True,
        )

    # Apply 0004 — the backfill runs.
    command.upgrade(cfg, "head")

    with engine.begin() as conn:
        assert _verified(conn, fb_with_email) is True, (
            "pre-fix Facebook row with a non-null email must be backfilled"
        )
        assert _verified(conn, fb_no_email) is False, (
            "Facebook row with no email has nothing to verify — must stay false"
        )
        assert _verified(conn, google_unverified) is False, (
            "Google row must be untouched — backfill is Facebook-scoped"
        )
        assert _verified(conn, apple_unverified) is False, (
            "Apple row must be untouched — backfill is Facebook-scoped"
        )
        assert _verified(conn, fb_already_verified) is True, (
            "already-verified Facebook row must stay verified (idempotency)"
        )

    engine.dispose()


def test_backfill_downgrade_is_documented_no_op(pg_url: str) -> None:
    """0004's `downgrade()` is a deliberate no-op (exact reversal is
    unsafe — a backfilled pre-#69 row is indistinguishable from a
    natively-verified post-#69 row). Lock that behaviour in so a future
    PR that "fixes" the downgrade into a blanket flip-back is forced to
    confront that it would clobber legitimately-verified rows.
    """
    cfg = _alembic_config(pg_url)
    engine = create_engine(pg_url)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0003")

    fb_with_email = uuid.uuid4()
    with engine.begin() as conn:
        _insert_user(
            conn,
            user_id=fb_with_email,
            provider="facebook",
            sub=f"fb-noop-{fb_with_email.hex[:8]}",
            email="noop-fb@example.com",
            email_verified=False,
        )

    command.upgrade(cfg, "head")
    with engine.begin() as conn:
        assert _verified(conn, fb_with_email) is True

    # Downgrade one step (0004 -> 0003). The no-op must NOT flip the row
    # back, and must not raise.
    command.downgrade(cfg, "0003")
    with engine.begin() as conn:
        assert _verified(conn, fb_with_email) is True, (
            "downgrade is a documented no-op — it must not revert the flag"
        )

    # Re-applying the migration after the no-op downgrade is harmless.
    command.upgrade(cfg, "head")
    with engine.begin() as conn:
        assert _verified(conn, fb_with_email) is True

    engine.dispose()
