"""Single-use enforcement for `link_token`s consumed by `POST /api/auth/link`.

Background. `app.auth.link.issue_link_token` mints each link token with a
unique `jti` (uuid4 hex). Without a server-side record of consumed `jti`s,
a leaked link token would be replayable for the full TTL (10 minutes).
This table records each `jti` consumed by the link route and the route
rejects replays with 401.

Storage choice — table vs Redis: Redis isn't yet wired into the auth path
beyond health checks (per `app/auth/link.py` lines 7-9 docstring +
`docs/auth.md` § "Single-use enforcement"). Adding a Redis dependency for
one consumer would be the wrong direction; a small table with `jti` PK is
self-contained, queryable, and satisfies the AC. Tech-lead's slice-4
dispatch comment on #11 (2026-05-10) makes the same recommendation.

Cleanup of expired rows is left as a future concern — rows are tiny
(~36 bytes for the jti hex + a couple of timestamps), and a future sweeper
job (or a periodic `DELETE WHERE expires_at < now()`) can be added when
the rate of link consumption justifies it. For now, accumulation is fine.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ConsumedLinkToken(Base):
    """One row per consumed `link_token.jti`. Replay attempts hit the PK
    and are rejected by the route with 401.
    """

    __tablename__ = "consumed_link_tokens"

    # `jti` is uuid4 hex (32 chars), but we leave headroom in case a future
    # issuer uses a different format. PK rather than a separate `id` so the
    # uniqueness constraint and the lookup index are the same physical
    # structure; replay attempts collide on the PK on insert.
    jti: Mapped[str] = mapped_column(String(64), primary_key=True)

    consumed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Copied from the link token's `exp` claim. Lets a future sweeper job
    # drop expired rows without re-decoding the original token (which we
    # don't store).
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
