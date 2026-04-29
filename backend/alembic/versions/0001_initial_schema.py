"""initial schema: users, listings, listing_images, listing_ar_assets, transactions

Revision ID: 0001
Revises:
Create Date: 2026-04-29 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("provider_user_id", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("email_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("avatar_url", sa.String(500), nullable=True),
        sa.Column("can_sell", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("can_purchase", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("seller_rating", sa.Numeric(3, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("provider", "provider_user_id", name="uq_users_provider_sub"),
    )

    op.create_table(
        "listings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("seller_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("brand", sa.String(120), nullable=True),
        sa.Column("category", sa.String(60), nullable=False),
        sa.Column("size", sa.String(40), nullable=True),
        sa.Column("condition", sa.String(20), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), server_default="USD", nullable=False),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("price_cents > 0", name="ck_listings_price_positive"),
    )
    op.create_index("ix_listings_seller_id", "listings", ["seller_id"])
    op.create_index("ix_listings_brand", "listings", ["brand"])
    op.create_index("ix_listings_category", "listings", ["category"])
    op.create_index("ix_listings_status", "listings", ["status"])
    op.create_index("ix_listings_status_created", "listings", ["status", sa.text("created_at DESC")])

    op.create_table(
        "listing_images",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "listing_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_listing_images_listing_id", "listing_images", ["listing_id"])

    op.create_table(
        "listing_ar_assets",
        sa.Column(
            "listing_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("glb_low_key", sa.String(500), nullable=False),
        sa.Column("glb_high_key", sa.String(500), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("listings.id"), nullable=False),
        sa.Column("buyer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("seller_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("buyer_id <> seller_id", name="ck_transactions_no_self_buy"),
    )
    op.create_index("ix_transactions_listing_id", "transactions", ["listing_id"])
    op.create_index("ix_transactions_buyer_id", "transactions", ["buyer_id"])
    op.create_index("ix_transactions_seller_id", "transactions", ["seller_id"])
    op.create_index("ix_transactions_status", "transactions", ["status"])


def downgrade() -> None:
    op.drop_table("transactions")
    op.drop_table("listing_ar_assets")
    op.drop_table("listing_images")
    op.drop_table("listings")
    op.drop_table("users")
