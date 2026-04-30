import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, LargeBinary, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RefreshToken(Base):
    """Server-side record of an issued refresh token.

    The plaintext token value is never stored — only its hash. Refresh tokens
    are rotated on each use: the prior row is marked `revoked_at = now()` and
    a fresh row is inserted. Reuse of a revoked token is the signal for the
    auth layer to revoke every token belonging to `user_id` (likely theft).

    Schema follows RFC 0001 § Schema additions.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def is_expired(self, now: datetime | None = None) -> bool:
        current = now if now is not None else datetime.now(UTC)
        return current >= self.expires_at

    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def is_active(self, now: datetime | None = None) -> bool:
        return not self.is_revoked() and not self.is_expired(now)

    def revoke(self, now: datetime | None = None) -> None:
        """Mark this token revoked. No-op if already revoked."""
        if self.revoked_at is None:
            self.revoked_at = now if now is not None else datetime.now(UTC)
