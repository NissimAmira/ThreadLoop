"""account linking: user_identities + consumed_link_tokens

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-10 00:00:00

Schema additions for the slice-4 account-linking flow (#18):

- `user_identities` becomes the source of truth for "all (provider,
  provider_user_id) tuples this user holds." Callbacks look identities
  up here rather than scanning `users.(provider, provider_user_id)`
  directly. The denormalized columns on `users` stay populated and
  represent the primary identity (the original signup provider). See
  `app/models/user_identity.py` for the full rationale.

- `consumed_link_tokens` records each `jti` consumed by
  `POST /api/auth/link` and lets the route reject replays. Without
  this, a leaked link token would be replayable for the full TTL.

Backfill: every existing `users` row gets a matching `user_identities`
row so callbacks keep finding existing users via the new lookup path
without a flag-day. The backfill is idempotent (no-op on a fresh DB
where `users` is empty) and reversible — `downgrade()` drops both
tables.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("provider_user_id", sa.String(255), nullable=False),
        sa.Column(
            "linked_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "provider",
            "provider_user_id",
            name="uq_user_identities_provider_sub",
        ),
    )
    op.create_index(
        "ix_user_identities_user_id", "user_identities", ["user_id"]
    )

    # Backfill the primary identity for every existing user. Idempotent on a
    # fresh DB (no rows to copy). Uses gen_random_uuid() from pgcrypto if
    # available, else relies on the column's app-side default by inserting
    # via a server-generated uuid expression. Postgres 13+ ships
    # gen_random_uuid() in core (no extension needed).
    op.execute(
        """
        INSERT INTO user_identities (id, user_id, provider, provider_user_id, linked_at)
        SELECT gen_random_uuid(), id, provider, provider_user_id, created_at
          FROM users
        """
    )

    op.create_table(
        "consumed_link_tokens",
        sa.Column("jti", sa.String(64), primary_key=True),
        sa.Column(
            "consumed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("consumed_link_tokens")
    op.drop_index("ix_user_identities_user_id", table_name="user_identities")
    op.drop_table("user_identities")
