# RFC 0001: SSO authentication (Google / Apple / Facebook)

- **Status:** Partially implemented (slice 1 live; slices 2–5 in flight)
- **Author:** Nissim
- **Created:** 2026-04-30
- **Approved:** 2026-04-30
- **Tracking issue:** #11 (Epic)
- **Slice 1 shipped:** 2026-05-03 — Google web sign-in end-to-end
  (PR #43 FE + PR #41 BE + PR #47 wire shape; ADR 0009 captures the
  camelCase wire decision). See `docs/auth.md` "Already landed" for
  the per-PR breakdown.
- **Remaining slices:** Apple web button (#38), Facebook web button
  (#39), `link_required` UI flow (#40, paired with BE #18), mobile
  SDK integration (#20). RFC scope is unchanged; rollout proceeds
  per the slice-by-slice plan below.

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

1. Users can sign in via Google, Apple, or Facebook from web and mobile.
2. No passwords stored or accepted, anywhere.
3. Apple "Hide my Email" relay addresses are handled correctly — the
   account is linkable to its real email if the user later confirms.
4. Sessions survive browser/app restarts via a refresh-token mechanism;
   inactive sessions expire after 30 days.
5. The session-validation middleware is one line of code per protected
   route (Depends-style for FastAPI).
6. iOS App Store compliance: Sign in with Apple is offered alongside any
   other social login (Guideline 4.8).

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

**Sign-in (web).** A single sign-in page with three buttons. Clicking
launches the provider's hosted flow in the same tab. On callback, the
backend issues a session and the page redirects to the original
destination.

**Sign-in (mobile).** Apple uses native `expo-apple-authentication` (per
4.8). Google and Facebook use `expo-auth-session` for in-app browser
flows. On callback, the same backend endpoints issue a session.

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
   Land migrations.
2. **Web sign-in page.** Wire to BE. Flag still off — can be tested via
   compose stack.
3. **Flip the flag in staging environment.** (Triggers Phase 2 of the
   DevOps roadmap — staging environment must exist by this point.)
4. **Mobile sign-in.** Follows web; reuses the same callback endpoints.
5. **Flip the flag in prod** once all three platforms are validated in
   staging.

Each step is a separate PR. The flag pattern keeps each PR
independently mergeable.

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

- [ ] User can sign in via Google on web (Chrome, Firefox, Safari).
- [ ] User can sign in via Apple on web.
- [ ] User can sign in via Facebook on web.
- [ ] User can sign in via Apple on iOS (native flow).
- [ ] User can sign in via Google on iOS and Android.
- [ ] User can sign in via Facebook on iOS and Android.
- [ ] Apple "Hide my Email" relay addresses don't error and create a
      valid account.
- [ ] Account-linking prompt fires when email collision is detected
      across providers.
- [ ] Session expires after 30 days of inactivity (refresh token expiry).
- [ ] Logout revokes the refresh token; subsequent /api/auth/refresh
      returns 401.
- [ ] /api/me returns the current user with the correct shape per the
      OpenAPI spec.
- [ ] Test coverage: integration tests per provider against test JWKS;
      unit tests for the session middleware.
- [ ] OpenAPI spec is updated and Schemathesis (when wired) finds no
      drift.
- [ ] `docs/auth.md` "What's not implemented yet" reflects what shipped.

## Out of scope follow-ups

- Two-step linking when Apple relay is unmasked later.
- Account deletion + GDPR data export.
- MFA for sellers handling payouts.
- "Sign in with Microsoft" / arbitrary OIDC.
- Programmatic API tokens (for third-party integrations).
