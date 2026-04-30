"""Unit tests for the RefreshToken model.

Covers the helper semantics that the auth layer (issued in tasks #14/#15/#16/#17)
will rely on — expiry / revocation / rotation — plus a metadata sanity check
that the table is registered with the schema RFC 0001 specifies.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import ForeignKey, LargeBinary
from sqlalchemy.dialects.postgresql import UUID

from app.models import RefreshToken


def _make_token(
    *,
    expires_in: timedelta = timedelta(days=30),
    revoked: bool = False,
    issued_at: datetime | None = None,
) -> RefreshToken:
    issued = issued_at if issued_at is not None else datetime.now(UTC)
    token = RefreshToken(
        user_id=__import__("uuid").uuid4(),
        token_hash=b"\x00" * 32,
        issued_at=issued,
        expires_at=issued + expires_in,
    )
    if revoked:
        token.revoked_at = issued
    return token


# ----- helper semantics ------------------------------------------------------


def test_freshly_issued_token_is_active() -> None:
    token = _make_token()
    assert token.is_active() is True
    assert token.is_revoked() is False
    assert token.is_expired() is False


def test_token_past_expires_at_is_expired() -> None:
    issued = datetime.now(UTC) - timedelta(days=31)
    token = _make_token(issued_at=issued, expires_in=timedelta(days=30))
    assert token.is_expired() is True
    assert token.is_active() is False


def test_token_at_exact_expires_at_is_expired() -> None:
    """Boundary: `now == expires_at` counts as expired."""
    now = datetime.now(UTC)
    token = RefreshToken(
        user_id=__import__("uuid").uuid4(),
        token_hash=b"\x01" * 32,
        issued_at=now - timedelta(days=30),
        expires_at=now,
    )
    assert token.is_expired(now=now) is True


def test_revoke_sets_timestamp_and_disables_token() -> None:
    token = _make_token()
    assert token.is_active() is True

    token.revoke()
    assert token.is_revoked() is True
    assert token.is_active() is False
    assert token.revoked_at is not None


def test_revoke_is_idempotent() -> None:
    """Calling revoke twice must not move the timestamp."""
    token = _make_token()
    first = datetime(2026, 1, 1, tzinfo=UTC)
    token.revoke(now=first)

    second = datetime(2026, 6, 1, tzinfo=UTC)
    token.revoke(now=second)

    assert token.revoked_at == first


def test_rotation_pattern() -> None:
    """The rotation flow used by /api/auth/refresh: revoke old, issue new.

    The new token must be independently active even though it shares the
    same user_id with the revoked one.
    """
    user_id = __import__("uuid").uuid4()
    now = datetime.now(UTC)

    old = RefreshToken(
        user_id=user_id,
        token_hash=b"\xaa" * 32,
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(days=29),
    )
    old.revoke(now=now)

    new = RefreshToken(
        user_id=user_id,
        token_hash=b"\xbb" * 32,
        issued_at=now,
        expires_at=now + timedelta(days=30),
    )

    assert old.is_active() is False
    assert old.is_revoked() is True
    assert new.is_active() is True
    assert new.user_id == old.user_id
    assert new.token_hash != old.token_hash


def test_explicit_now_is_honored() -> None:
    """`is_expired` / `is_active` must accept an injected clock for determinism."""
    issued = datetime(2026, 1, 1, tzinfo=UTC)
    token = RefreshToken(
        user_id=__import__("uuid").uuid4(),
        token_hash=b"\x02" * 32,
        issued_at=issued,
        expires_at=issued + timedelta(days=30),
    )
    assert token.is_expired(now=issued + timedelta(days=15)) is False
    assert token.is_expired(now=issued + timedelta(days=30, seconds=1)) is True


# ----- schema sanity ---------------------------------------------------------


@pytest.fixture
def refresh_tokens_table() -> object:
    return RefreshToken.__table__


def test_table_name_matches_rfc(refresh_tokens_table: object) -> None:
    assert refresh_tokens_table.name == "refresh_tokens"  # type: ignore[attr-defined]


def test_required_columns_present(refresh_tokens_table: object) -> None:
    columns = {c.name for c in refresh_tokens_table.columns}  # type: ignore[attr-defined]
    assert columns == {
        "id",
        "user_id",
        "token_hash",
        "issued_at",
        "expires_at",
        "revoked_at",
    }


def test_token_hash_is_bytea_and_required(refresh_tokens_table: object) -> None:
    col = refresh_tokens_table.columns["token_hash"]  # type: ignore[attr-defined]
    assert isinstance(col.type, LargeBinary)
    assert col.nullable is False


def test_user_id_is_uuid_with_cascade_fk(refresh_tokens_table: object) -> None:
    col = refresh_tokens_table.columns["user_id"]  # type: ignore[attr-defined]
    assert isinstance(col.type, UUID)
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    fk: ForeignKey = fks[0]
    assert fk.column.table.name == "users"
    assert fk.ondelete == "CASCADE"


def test_user_id_is_indexed(refresh_tokens_table: object) -> None:
    """RFC 0001 requires `ix_refresh_tokens_user_id` for lookup by user."""
    col = refresh_tokens_table.columns["user_id"]  # type: ignore[attr-defined]
    assert col.index is True


def test_token_hash_is_unique(refresh_tokens_table: object) -> None:
    constraint_names = {
        c.name
        for c in refresh_tokens_table.constraints  # type: ignore[attr-defined]
    }
    assert "uq_refresh_tokens_token_hash" in constraint_names


def test_expires_at_required_and_revoked_at_nullable(refresh_tokens_table: object) -> None:
    cols = refresh_tokens_table.columns  # type: ignore[attr-defined]
    assert cols["expires_at"].nullable is False
    assert cols["revoked_at"].nullable is True
