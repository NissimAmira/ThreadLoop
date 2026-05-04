# RFC 0001: SSO authentication (Google / Apple / Facebook)

- **Status:** Partially implemented (Google web live; Apple deferred — see § Deferred providers; Facebook web + mobile pending)
- **Author:** Nissim
- **Created:** 2026-04-30
- **Approved:** 2026-04-30
- **Revised:** 2026-05-04 — Apple descoped from the active Epic. The
  Apple BE callback and FE button are already in main and gated off
  by default per `docs/auth.md` § "Per-provider gating"; they stay
  dormant until the user enrolls in the Apple Developer Program. See
  § "Deferred providers" below.
- **Tracking issue:** #11 (Epic)
- **Slice 1 shipped:** 2026-05-03 — Google web sign-in end-to-end
  (PR #43 FE + PR #41 BE + PR #47 wire shape; ADR 0009 captures the
  camelCase wire decision). See `docs/auth.md` "Already landed" for
  the per-PR breakdown.
- **Slice 2 shipped (deferred from product):** 2026-05-04 — Apple
  web sign-in code merged in PR #55 (#38). Code is in main, disabled
  by default (`APPLE_ENABLED=false`). Stays deferred per § "Deferred
  providers" below.
- **Remaining slices in this Epic:** Facebook web button (#39),
  `link_required` UI flow (#40, paired with BE #18), mobile SDK
  integration (#20, Apple-on-iOS dropped from scope).

## TL;DR

ThreadLoop authenticates users exclusively via Google, Apple, or Facebook
SSO. There are no passwords. This RFC specifies the user-visible flows,
session model, account-linking semantics, and the per-platform technical
approach (web + iOS + Android). It is the first feature work after the
infra scaffold — design choices set here govern every subsequent
auth-related change.

## Problem

ThreadLoop has the auth schema (`users.provider`, `provider_user_id`)
but no actual sign-in. Users cannot create accounts, sessions, or any
state. Until this ships, the marketplace functionality (listings,
transactions, AR) cannot meaningfully be tested by anyone but the
developer.

## Goals

1. Users can sign in via Google or Facebook from web and mobile. (Apple
   is implemented but disabled in this Epic — see § "Deferred
   providers".)
2. No passwords stored or accepted, anywhere.
3. Apple "Hide my Email" relay addresses are handled correctly when
   Apple is re-activated — the account is linkable to its real email if
   the user later confirms. (Verifier and bypass logic already shipped
   in #15.)
4. Sessions survive browser/app restarts via a refresh-token mechanism;
   inactive sessions expire after 30 days.
5. The session-validation middleware is one line of code per protected
   route (Depends-style for FastAPI).
6. iOS App Store compliance: Sign in with Apple **will** be offered
   alongside any other social login (Guideline 4.8) once the user
   enrolls in the Apple Developer Program. The 4.8 obligation only
   bites at App Store submission; while the iOS app is unsubmitted,
   shipping Google + Facebook only is permissible. See § "Deferred
   providers".

## Deferred providers

> **Tracker:** [#57](https://github.com/NissimAmira/ThreadLoop/issues/57) — *"Re-activate Apple Sign In once enrolled in Apple Developer Program"* (P3, tech debt). The re-activation procedure below is mirrored on that issue as the durable record; if this RFC and the issue ever drift, the RFC is canonical.

### Apple — implemented, gated off, awaiting Apple Developer Program enrollment

Apple sign-in is fully implemented end-to-end on backend and web, but
**disabled by default in this Epic and in every deploy until the
project owner enrolls in the Apple Developer Program**.

**What's in main today:**

- BE: `POST /api/auth/apple/callback` (#15, PR #33), Apple JWKS
  verifier, `is_private_email` Hide-My-Email bypass, ES256
  `client_secret` JWT generator with 50-minute in-process cache.
- FE: `frontend-web/src/auth/apple.ts` SDK loader, Apple button on
  `/sign-in` (#38, PR #55), Cypress smoke (`sign-in-apple.cy.ts`).
- Tests pass against a stubbed Apple JWKS.

**What's gating it off:**

- Backend `APPLE_ENABLED` flag defaults to `false`
  (`backend/app/config.py`). With this flag off, both
  `POST /api/auth/apple/callback` and the `Settings` validation that
  would require `APPLE_CLIENT_ID` / `APPLE_TEAM_ID` / `APPLE_KEY_ID`
  / `APPLE_PRIVATE_KEY` are inert. See `docs/auth.md` § "Per-provider
  gating" for the full behaviour matrix.
- Frontend: when `VITE_APPLE_CLIENT_ID` is unset, the Apple button on
  `/sign-in` renders in its disabled-fallback state.

**Why deferred:** enabling Apple in production requires:

- Enrollment in the Apple Developer Program (~$99 USD per year),
  required to obtain the Service ID and `.p8` key.
- Verified-domain configuration in the Apple Developer portal (the
  redirect URL has to be served from a domain Apple has verified
  against the Service ID), which is not free of effort even after
  enrollment.

The project owner has decided not to enroll until preparing for App
Store submission of the iOS app. App Store Guideline 4.8 mandates
Sign in with Apple at submission time if any other social login is
offered; until then, shipping Google + Facebook only is permissible.
A web-only deployment without an iOS app does not trigger 4.8.

**Re-activation procedure** (when the owner enrolls):

1. Provision Apple Service ID + `.p8` key in the Apple Developer
   portal.
2. Set BE secrets: `APPLE_CLIENT_ID`, `APPLE_TEAM_ID`,
   `APPLE_KEY_ID`, `APPLE_PRIVATE_KEY` (PEM contents of the `.p8`).
3. Set FE secret: `VITE_APPLE_CLIENT_ID` (must equal the BE's
   `APPLE_CLIENT_ID` Service ID; mismatched values fail at the JWKS
   `aud` check).
4. Flip `APPLE_ENABLED=true` (BE) — `Settings` validates the secrets
   are present at boot.
5. The button on `/sign-in` lights up automatically once
   `VITE_APPLE_CLIENT_ID` is set in the build.
6. Run the existing Cypress smoke (`sign-in-apple.cy.ts`) against
   real credentials, then validate in staging before flipping in
   production. Same staging-before-prod cadence as the master
   `AUTH_ENABLED` flag.

**Why we did not rip the Apple code out:** the per-provider gating
already keeps it dormant; ripping it out would be churn that the
owner would have to undo by re-implementing slice 2 from scratch
when they do enroll. The cost of leaving it in is the maintenance
burden on a small surface that's covered by tests but not exercised
in any active deployment — acceptable.

### Mobile native Apple sign-in

iOS native Apple sign-in (via `expo-apple-authentication`) is
descoped from #20 in this Epic. The iOS app will sign in via Google
and Facebook (both via `expo-auth-session`) until the App Store
submission cycle starts. App Store Guideline 4.8 **only applies at
submission time**, not during development; a Google + Facebook
mobile build is shippable internally and to TestFlight without 4.8
compliance, and Apple sign-in is added back as a slice in the App
Store submission Epic.

## Non-goals

- Email/password fallback. **Explicitly rejected.** Will not be added.
- Magic-link sign-in. Same.
- Multi-factor authentication. Defer until we have payouts (where MFA
  for sellers is the right risk-tier).
- SSO with arbitrary OIDC providers (Microsoft, GitHub, etc.). Not now.
- Account deletion / data export flow. Separate compliance epic.
- Two-step account linking when an Apple relay is unmasked later.
  Punt to a follow-up.

## Proposal

### User-visible flows

**Sign-in (web).** A single sign-in page with one button per active
provider — currently Google and (once #39 lands) Facebook. The Apple
button is rendered only when `VITE_APPLE_CLIENT_ID` is set; in the
default deferred-Apple configuration it is not visible to end users.
Clicking launches the provider's hosted flow in the same tab. On
callback, the backend issues a session and the page redirects to the
original destination.

**Sign-in (mobile).** Google and Facebook use `expo-auth-session` for
in-app browser flows on both iOS and Android. Native Apple sign-in
via `expo-apple-authentication` is descoped from this Epic and ships
with the App Store submission Epic per Guideline 4.8 (which is only
enforced at submission time, not during development). On callback,
the same backend endpoints issue a session.

**Sign-out.** Clears refresh cookie + revokes the refresh token
server-side. Access JWT will simply expire.

**Account linking.** When a user signs in via provider B with a verified
email matching an existing account from provider A, the backend does
**not** silently merge. Instead, it issues a "pending link" session and
the UI prompts: *"This email belongs to an account that signed up with
{provider A}. Sign in with {A} now to link them."* If the user
re-authenticates with A, both providers point to the same `users` row.
Apple relay addresses bypass this check (no email match possible).

### API shape

Three callback endpoints (one per provider) plus session management.
Full schemas land in `shared/openapi.yaml` per the contract-first rule.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/auth/google/callback` | Exchange Google ID token for ThreadLoop session |
| POST | `/api/auth/apple/callback` | Exchange Apple ID token + auth code for session |
| POST | `/api/auth/facebook/callback` | Exchange Facebook access token for session |
| POST | `/api/auth/refresh` | Issue new access JWT from refresh cookie |
| POST | `/api/auth/logout` | Revoke refresh token, clear cookie |
| GET  | `/api/me` | Return the authenticated user |

### Session model

- **Access JWT** — 15-minute lifetime, returned in response body, sent as
  `Authorization: Bearer <jwt>` on requests. Signed HS256 with a server
  secret.
- **Refresh token** — 30-day rolling lifetime, stored as an httpOnly,
  Secure, SameSite=Lax cookie. Server-side: stored in `refresh_tokens`
  table with `user_id`, `token_hash`, `issued_at`, `expires_at`,
  `revoked_at`. Rotated on each refresh (old token revoked, new one
  issued).
- **Revocation:** logout revokes the current refresh token. A
  user-controlled "sign out all sessions" button revokes all of theirs.

### Schema additions

A new `refresh_tokens` table:

```sql
CREATE TABLE refresh_tokens (
  id           uuid PRIMARY KEY,
  user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash   bytea NOT NULL UNIQUE,
  issued_at    timestamptz NOT NULL DEFAULT now(),
  expires_at   timestamptz NOT NULL,
  revoked_at   timestamptz
);
CREATE INDEX ix_refresh_tokens_user_id ON refresh_tokens(user_id);
```

`users` already has `provider`, `provider_user_id`, and `email`. No
changes needed there.

### Failure modes

- Provider JWKS unreachable → return 503; client retries.
- Token signature invalid → 401, log structured event, no PII in log.
- Token expired → 401; client retries the SSO flow.
- Email matches existing user from different provider → 200 with
  `link_required: true` and a temporary link token; user re-authenticates
  with the original provider to confirm.
- Refresh token reuse (the old one comes in after rotation) → revoke
  ALL of that user's refresh tokens (likely token theft); force re-auth.

## Alternatives considered

### Email/password with bcrypt

Standard approach. Rejected because:
- Credential storage is a permanent breach surface.
- Password reset flows (forgot/reset/change) add three more screens.
- Email verification adds another flow.
- App Store compliance still requires Apple SSO if any social login is
  offered, so we'd be building two auth systems.

### Magic-link only

Email a one-time link, no password. Attractive for simplicity. Rejected
because:
- Mobile UX is bad (email → switch app → click → switch back).
- Doesn't satisfy Apple 4.8 — still need Apple SSO for iOS.
- Adds an email-deliverability dependency on the critical path.

### Auth0 / Clerk / WorkOS

Outsource the whole problem. Attractive for portfolio simplicity.
Rejected because:
- Demonstrating real OIDC implementation is itself a portfolio signal.
- Adds vendor lock-in for the most user-visible flow.
- Free tiers have user limits that would constrain organic demo growth.
- Locks us into their session model.

### Server sessions (no JWT)

Issue a session ID, look it up server-side. Rejected because:
- Adds a Redis/DB lookup on every request to a horizontally scaled API.
- JWT solves the same problem with cryptographic verification.
- Refresh tokens give us the revocation lever JWT alone lacks.

## Risks and open questions

- **Risk: Apple `client_secret` is a JWT signed with a team key that
  rotates every 6 months.** Mitigation: scheduled job rotates and stores
  the secret; alert if rotation fails. Open question: implement now, or
  punt to ops backlog and rotate manually for the first ~6 months?
- **Risk: Account-linking UI is ambiguous.** Apple's relay address
  means we sometimes literally cannot tell if it's the same person.
  Mitigation: when in doubt, treat them as separate accounts; offer an
  explicit "link these accounts" feature in user settings later.
- **Open question: which web library?** Google Identity Services is
  official; Apple's JS SDK is fine; Facebook's Login SDK is fine. Should
  we wrap them or use directly? Tech-lead to decide.
- **Open question: do we redirect or use popup flow on web?** Both are
  viable; redirect is more reliable on mobile-Safari. Proposed: redirect.

## Rollout plan

1. **BE-only first.** Implement all three callbacks + session model
   behind feature flag `AUTH_ENABLED=false` (return 404 by default).
   Land migrations. *(Done.)*
2. **Web sign-in page.** Wire to BE. Flag still off — can be tested via
   compose stack. *(Done.)*
3. **Flip the master flag in staging environment.** (Triggers Phase 2
   of the DevOps roadmap — staging environment must exist by this
   point.)
4. **Mobile sign-in (Google + Facebook).** Follows web; reuses the
   same callback endpoints. Apple-on-iOS dropped from this slice
   per § "Deferred providers".
5. **Flip the master flag in prod** once Google + Facebook are
   validated in staging. **`APPLE_ENABLED` stays `false` in both
   staging and prod** until Apple Developer Program enrollment;
   re-activation procedure in § "Deferred providers".

Each step is a separate PR. The master `AUTH_ENABLED` flag plus
per-provider `<PROVIDER>_ENABLED` flags keep each PR independently
mergeable. Per-provider flags follow the same staging-before-prod
cadence as the master flag.

### Documentation impact

Per `docs/contributing.md` → "Documentation is part of done":
- `shared/openapi.yaml` — new auth endpoints + schemas.
- `system_design.md` — `refresh_tokens` table, session model section
  expanded.
- `docs/auth.md` — move items out of "What's not implemented yet"
  as they ship.
- `docs/adrs/0002-sso-only-auth.md` — already exists; reference here.
- README roadmap — tick the SSO box.

## Acceptance criteria

Active scope (Google + Facebook on web and mobile):

- [x] User can sign in via Google on web (Chrome, Firefox, Safari).
      *(Slice 1 shipped 2026-05-03.)*
- [ ] User can sign in via Facebook on web.
- [ ] User can sign in via Google on iOS and Android.
- [ ] User can sign in via Facebook on iOS and Android.
- [ ] Account-linking prompt fires when email collision is detected
      across providers (Google ↔ Facebook in this Epic — Facebook's
      side never fires in practice because the Graph API doesn't
      expose `email_verified`; see `docs/auth.md` § "Facebook
      specifics"). Apple ↔ Google linking is shipped in code but
      unreachable while `APPLE_ENABLED=false`.
- [ ] Session expires after 30 days of inactivity (refresh token expiry).
- [ ] Logout revokes the refresh token; subsequent /api/auth/refresh
      returns 401.
- [ ] /api/me returns the current user with the correct shape per the
      OpenAPI spec.
- [ ] Test coverage: integration tests per active provider against
      test JWKS; unit tests for the session middleware.
- [ ] OpenAPI spec is updated and Schemathesis (when wired) finds no
      drift.
- [ ] `docs/auth.md` "What's not implemented yet" reflects what shipped.

Deferred (do NOT block Epic closure — see § "Deferred providers"):

- [~] User can sign in via Apple on web. *(Code shipped in #38 / PR
      #55; gated off by default. Re-activates when the owner enrolls
      in the Apple Developer Program.)*
- [~] User can sign in via Apple on iOS (native flow). *(Out of scope
      for #20 in this Epic; ships in the App Store submission Epic
      per Guideline 4.8.)*
- [~] Apple "Hide my Email" relay addresses don't error and create a
      valid account. *(Logic shipped in #15; reachable only with
      `APPLE_ENABLED=true`.)*

## Out of scope follow-ups

- **Apple sign-in re-activation** — flip `APPLE_ENABLED=true` in
  staging then production once the owner enrolls in the Apple
  Developer Program. Procedure documented in § "Deferred providers".
  Tracked outside this Epic; will be its own slice when triggered.
- **iOS native Apple sign-in** — `expo-apple-authentication`
  integration on the mobile app. Lands in the App Store submission
  Epic per Guideline 4.8.
- Two-step linking when Apple relay is unmasked later.
- Account deletion + GDPR data export.
- MFA for sellers handling payouts.
- "Sign in with Microsoft" / arbitrary OIDC.
- Programmatic API tokens (for third-party integrations).
