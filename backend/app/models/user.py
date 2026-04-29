import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("provider", "provider_user_id", name="uq_users_provider_sub"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_user_id: Mapped[str] = mapped_column(String(255), nullable=False)

    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    can_sell: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_purchase: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    seller_rating: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
