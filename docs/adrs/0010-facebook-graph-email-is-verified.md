# ADR 0010: Treat a Facebook Graph `/me` email as verified for cross-provider collision detection

- **Status:** Accepted
- **Date:** 2026-05-20
- **Context links:** RFC #0001 (auth-sso), Epic #11, ADR #0002 (SSO-only
  auth), defect test "A5", `backend/app/auth/facebook.py`,
  `backend/app/routers/auth.py`, `docs/auth.md` § "Facebook specifics"

## Context

Epic #11 slice 4 shipped a cross-provider account-linking flow: when a
user signs in with provider B using a verified email already registered
to a provider-A account, the callback returns a `link_required` envelope
and the web `LinkAccountsDialog` walks the user through re-authenticating
with provider A to merge the two identities into one `users` row.

Manual end-to-end test "A5" found the flow has **zero reachable entry
point** in the Google + Facebook provider configuration that is live in
`main`:

1. `backend/app/auth/facebook.py` hard-codes `email_verified=False` on
   every `FacebookIdentity` (`_parse_me_response`), with the documented
   reasoning that Facebook's Graph API `/me` response carries no
   `verified` flag, so we "cannot make the same guarantee Google and
   Apple's ID tokens give us."
2. Both the Google and Facebook cross-provider collision checks in
   `backend/app/routers/auth.py` require `identity.email_verified`
   truthy **and** the candidate existing row to satisfy
   `User.email_verified.is_(True)`.
3. Because every Facebook identity is `email_verified=False`, the
   Facebook callback's collision `if` is dead — it never enters, the
   callback falls through to "create a new user," and a Facebook
   sign-in on an email already held by a Google account silently
   creates a **second, independent `users` row**.
4. The same `email_verified=False` is persisted to `users.email_verified`
   for every Facebook-primary user. So the exemption is **bidirectional**:
   a later Google sign-in on a Facebook-first email also fails the
   `User.email_verified.is_(True)` filter and likewise creates a
   duplicate.
5. `docs/auth.md` § "Facebook specifics" framed this as deliberate and
   pointed at the linking flow as the escape hatch — "account merging
   across Facebook ↔ Google/Apple is exclusively user-initiated through
   the linking flow." But `LinkAccountsDialog` only opens on a
   `link_required` envelope. No callback ever emits one for Facebook, so
   there is no `linkToken`, so there is **no path — automatic or
   user-initiated — to start the linking flow** whenever a Facebook
   account is on either side of the collision.

Net effect in the shipped provider config (Apple is gated off): slice 4
account-linking is wired but unreachable. The Epic #11 AC "account-
linking prompt fires when an email collision is detected across
providers" was validated only against the Apple ↔ Google collision path,
which is itself flag-off and undeployed.

The forces at play:

- **The original rationale is an anti-account-takeover guard.** If we
  matched on an *unverified* attacker-controlled email, an attacker who
  registered `victim@example.com` with a provider that doesn't verify
  email could trip `link_required` against the victim's real account.
- **But Facebook's `/me` does not return unverified emails.** Per
  Facebook's Graph API behaviour, `/me?fields=email` returns an email
  only once the user has confirmed it with Facebook; an unconfirmed
  email is omitted from the response entirely. The absence of a
  `verified` *flag* is not the same as the email being unverified — it
  is Facebook declining to echo a flag for a property that is already
  invariant on the wire.
- **The merge is gated regardless.** `link_required` only *starts* the
  flow. The actual merge at `POST /api/auth/link` re-verifies the
  *original* provider's credential and requires it to resolve to the
  existing user's primary `(provider, provider_user_id)`. An attacker
  holding only a Facebook account cannot complete a link to a
  Google-primary account — they would need the victim's Google
  credential. So even the worst case the original rationale guards
  against is structurally unreachable through `link_required` + `link`.
- **The current behaviour is the worse outcome.** Two `users` rows for
  one human is a data-integrity defect: split listings, split
  transaction history, ambiguous identity, and an account the user
  cannot consolidate.

## Decision

**Treat an email returned by Facebook's Graph API `/me` endpoint as
verified for the purpose of cross-provider collision detection.**

Concretely:

1. `backend/app/auth/facebook.py` — `_parse_me_response` sets
   `email_verified=True` **when, and only when, `/me` returned a
   non-empty `email`**. When the user declined the `email` scope and
   `/me` omits `email`, the identity remains `email=None`,
   `email_verified=False` (there is nothing to verify). The
   `FacebookIdentity` docstring and the module docstring are rewritten
   to state this reasoning.
2. The Google and Facebook collision branches in
   `backend/app/routers/auth.py` are unchanged in *structure* — the
   `identity.email and identity.email_verified` guard stays. The fix is
   that `identity.email_verified` is now genuinely `True` for a Facebook
   sign-in that carries an email, so the Facebook branch becomes live
   and the Google branch's `User.email_verified.is_(True)` filter now
   admits Facebook-first rows.
3. `users.email_verified` for a Facebook-primary user with an email is
   consequently persisted as `True` — which is what makes the exemption
   stop being bidirectional.

The decision is deliberately narrow: it concerns *only* the Facebook
verifier's `email_verified` value and the collision detection that
depends on it. It does not touch the linking-flow security model (the
`POST /api/auth/link` re-auth gate is unchanged and remains the actual
merge authority), and it does not change Google/Apple behaviour.

## Consequences

- **(+)** Slice 4 account-linking gains a real entry point in the live
  Google + Facebook config. A Google-then-Facebook (or Facebook-then-
  Google) sign-in on one email now returns `link_required` and the
  existing `LinkAccountsDialog` opens — no FE change required.
- **(+)** The bidirectional exemption is closed. A Facebook-first user
  who later signs in with Google is offered the link instead of
  silently getting a second account.
- **(+)** The fix is client-agnostic: the collision logic lives entirely
  in the route layer, so web and mobile both gain the entry point from
  the same change. No FE/mobile code change is needed.
- **(+)** Reversible: reverting `_parse_me_response` to
  `email_verified=False` restores the prior (defective) behaviour. Not a
  one-way door. No migration is involved.
- **(−)** New Facebook-primary rows created *after* this fix will have
  `email_verified=True`; rows created *before* it keep
  `email_verified=False`. This is a data-consistency seam — see the
  follow-up note below. It does not affect *new* collision detection
  (which reads the live identity, not the stored flag) but it does mean
  a pre-fix Facebook row won't be picked up as a collision *candidate*
  by a later Google sign-in until the row is backfilled.
- **(−)** We are trusting Facebook's documented Graph behaviour ("`/me`
  only returns confirmed emails") rather than an explicit per-response
  flag. If Facebook ever changes that behaviour, the guarantee weakens
  silently. Accepted: the `link` re-auth gate is the real backstop, and
  the same implicit trust already underpins our use of `/me` for the
  `(provider, provider_user_id)` identity itself.
- **(new constraint)** `docs/auth.md` § "Facebook specifics" must be
  rewritten — the "always `False`" and "exemption is bidirectional"
  paragraphs are now false. The closing PR of the fix Epic owns that
  edit. The RFC #0001 AC footnote ("Facebook's side never fires in
  practice") is also now false and must be corrected.

**Data backfill (handled outside this ADR).** The live dev DB has at
least two affected rows (the duplicate pair from test "A5"). Production
has not deployed Epic #11. The fix Epic includes a dedicated sub-task to
(a) backfill `email_verified=True` for existing Facebook-primary rows
that have a non-null email, and (b) decide the disposition of the
already-duplicated `nisimamira@gmail.com` pair (manual merge or delete
the orphan Facebook row in dev). The backfill is a data migration, not a
schema migration — it still ships as a reversible Alembic revision per
the repo's reversibility rule.

## Alternatives considered

**Option B — Keep `email_verified=False`; build a Facebook-specific
linking entry point.** Add a separate "this email may already have an
account — link it?" prompt that fires for Facebook sign-ins without
going through `link_required`. Rejected: it duplicates the entire
collision-detection + link-token machinery for one provider, the FE
would need a second linking trigger path, and it leaves the
bidirectional Google-side exemption unaddressed (a Facebook-first row
still wouldn't trip a later Google sign-in). It is strictly more code to
preserve a guard that doesn't guard anything.

**Option C — Add a real second-factor email verification step for
Facebook (send a confirmation link).** Rejected: it reintroduces an
email-ownership challenge, which is exactly the kind of email-bound flow
ADR #0002 ("SSO-only auth") rules out, and it is disproportionate —
Facebook already confirmed the email, we'd be re-confirming what's
already confirmed.

**Option D — Match on email regardless of `email_verified` on both
sides.** Drop the `email_verified` guard entirely from the collision
checks. Rejected: this *would* reintroduce the genuine account-takeover
vector for any future provider that returns unverified emails. The guard
is correct in principle; the bug is purely that Facebook was wrongly
classified as an unverified-email provider. Fix the classification, keep
the guard.
