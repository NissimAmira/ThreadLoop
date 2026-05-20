"""backfill facebook email_verified

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-20 00:00:00

Data migration for #70 (fix Epic for the "A5" account-linking defect).

#69 changed the Facebook verifier (`backend/app/auth/facebook.py`,
`_parse_me_response`) so that a Facebook identity carrying an email from
Graph `/me` is now `email_verified=True`. Facebook-primary `users` rows
created *after* #69 therefore persist `email_verified=true` natively and
are valid cross-provider collision candidates.

Rows created *before* #69 still carry `email_verified=false` — the value
hard-coded by the old verifier. A later Google sign-in on such an email
fails the `User.email_verified.is_(True)` filter in the collision check
(`backend/app/routers/auth.py`) and silently creates a duplicate account
instead of returning `link_required`. This revision backfills those
pre-fix rows so they too become valid collision candidates.

See `docs/adrs/0010-facebook-graph-email-is-verified.md` § "Data
backfill" for the decision context.

`upgrade()` is a pure data update — no schema change. `users.email_verified`
already exists (revision 0001).

`downgrade()` is a deliberate, documented no-op — see the function
docstring for why an exact reversal is unsafe.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Flip `email_verified` to true for pre-#69 Facebook-primary users.

    Scoped to `provider = 'facebook' AND email IS NOT NULL AND
    email_verified = false`:

    - `provider = 'facebook'` — Google/Apple rows are untouched; their
      `email_verified` already reflects the verified claim in their ID
      token.
    - `email IS NOT NULL` — a Facebook user who declined the `email`
      scope has nothing to verify; the post-#69 verifier leaves such an
      identity `email=None, email_verified=false`, so the backfill must
      match that and skip them.
    - `email_verified = false` — only rows still carrying the stale
      pre-#69 value are touched; this also makes the migration
      idempotent (a re-run is a no-op).
    """
    op.execute(
        """
        UPDATE users
           SET email_verified = true
         WHERE provider = 'facebook'
           AND email IS NOT NULL
           AND email_verified = false
        """
    )


def downgrade() -> None:
    """Documented no-op — exact reversal is unsafe for this backfill.

    `upgrade()` is a one-way boolean flip with no per-row marker. Once
    #69 is live, *new* Facebook-primary rows are natively
    `email_verified=true` (the verifier sets it). After this backfill
    runs, a backfilled pre-#69 row and a natively-verified post-#69 row
    are **indistinguishable** — both are
    `provider='facebook', email IS NOT NULL, email_verified=true`.

    A blanket downgrade UPDATE flipping every
    `provider='facebook' AND email IS NOT NULL` row back to false would
    therefore clobber legitimately-verified post-#69 rows, corrupting
    live data and re-opening the collision-detection defect for them.

    Recording the exact set of touched row IDs (e.g. in an audit table)
    would make the downgrade precise, but that is disproportionate
    machinery for a one-shot data backfill whose forward effect is
    already idempotent and consistent with the live verifier behaviour.

    A documented no-op is the correct, honest choice here and satisfies
    the CLAUDE.md reversibility rule for data migrations: re-running
    `upgrade()` after this no-op is harmless, and the no-op never
    corrupts data. A *silently wrong* downgrade would not be acceptable;
    this explicit `pass` with rationale is.

    See `docs/adrs/0010-facebook-graph-email-is-verified.md` § "Data
    backfill" and the #70 PR description for the full discussion.
    """
    pass
