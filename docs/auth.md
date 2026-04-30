# Authentication

ThreadLoop is **SSO-only**. There are no passwords stored anywhere.

## Why SSO-only

- **No credential storage** = no credential breach surface.
- **Better UX** — users sign in with the account they already have.
- **App Store compliance** — Apple requires Sign in with Apple if any other
  social login is offered (Guideline 4.8).
- **Fewer flows to maintain** — no signup form, password reset, email
  verification, MFA enrollment, etc.

## Feature flag — `AUTH_ENABLED`

Per RFC 0001 § Rollout plan step 1, the entire auth subsystem ships behind a
single boolean flag. While `AUTH_ENABLED=false` (the default), every
`/api/auth/*` route returns 404 — the implementation is in the binary but
unreachable. This lets us land each provider, the refresh / logout / `/me`
work, and account-linking incrementally without exposing half-built flows.

The flag is enforced as a router-level FastAPI dependency
(`require_auth_enabled` in `app/routers/auth.py`), not by conditionally
registering the router, so OpenAPI generation stays honest — the routes
still appear in `/docs` and the contract doesn't lie about what the
deployed binary will look like once the flag is flipped.

When `AUTH_ENABLED=true`, `Settings()` refuses to construct unless all of
`GOOGLE_CLIENT_ID`, `JWT_SIGNING_KEY`, and `REFRESH_TOKEN_HMAC_KEY` are set
non-empty. This catches the common misconfiguration where an unset
`GOOGLE_CLIENT_ID` would silently make every sign-in look like "your token
is invalid" (401) when the real fault is server config.

Rollout sequence (RFC 0001):
1. Implementation lands flag-off.
2. Web sign-in page lands flag-off.
3. Flag flipped on in **staging** (Phase 2 of the DevOps roadmap).
4. Mobile sign-in lands flag-off.
5. Flag flipped on in **prod** once all three platforms validate in staging.

## Supported providers

| Provider | SDK (web) | SDK (mobile) | Notes |
| --- | --- | --- | --- |
| Google | Google Identity Services | `expo-auth-session` | Standard OIDC. |
| Apple | Sign in with Apple JS | `expo-apple-authentication` (iOS native) | `client_secret` is a JWT signed with the team key (rotates ~6mo). |
| Facebook | Facebook Login SDK | `expo-auth-session` | Returns access token, not ID token — we exchange for the user profile via Graph API. |

## Flow

```
[Client]
    │ launches provider auth UI
    ▼
[Provider]
    │ returns ID token (Google/Apple) or access token (Facebook)
    ▼
[Client]
    │ POST /api/auth/{provider}/callback  { id_token | code | access_token }
    ▼
[FastAPI]
    │ verifies signature against provider JWKS
    │ extracts: sub, email, email_verified, name, picture
    │ upserts users(provider, provider_user_id)
    │ issues:
    │   - access JWT (15 min, in body)
    │   - refresh token (httpOnly, Secure, SameSite=Lax cookie, 30 days)
    ▼
[Client]
    │ stores access JWT in memory
    │ uses Authorization: Bearer <jwt> for API calls
    │ when 401, calls /api/auth/refresh (cookie sent automatically)
```

## Schema (relevant columns)

```sql
users (
    id                uuid primary key,
    provider          text not null,                  -- 'google' | 'apple' | 'facebook'
    provider_user_id  text not null,                  -- the provider's `sub` claim
    email             text,                           -- nullable: Apple may withhold
    email_verified    boolean not null default false,
    display_name      text not null,
    avatar_url        text,
    can_sell          boolean not null default false,
    can_purchase      boolean not null default true,
    seller_rating     numeric(3,2),
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now(),
    UNIQUE (provider, provider_user_id)
);

refresh_tokens (
    id           uuid primary key,
    user_id      uuid not null references users(id) on delete cascade,
    token_hash   bytea not null unique,               -- hash of the opaque token; plaintext never stored
    issued_at    timestamptz not null default now(),
    expires_at   timestamptz not null,                -- 30 days from issued_at
    revoked_at   timestamptz                          -- null = active; non-null = revoked
);
CREATE INDEX ix_refresh_tokens_user_id ON refresh_tokens(user_id);
```

### Refresh-token semantics

- **Opaque + hashed at rest.** The token sent to the client is a 256-bit
  base64url-encoded random value (`secrets.token_urlsafe(32)`); only its hash
  lives in `refresh_tokens.token_hash`. Comparison is hash-of-incoming vs
  stored hash.
- **Hash function: HMAC-SHA-256, keyed with `REFRESH_TOKEN_HMAC_KEY`.** Chosen
  over Argon2id because the input is a 256-bit cryptographically random value
  the user never sees — the threat model is "DB leak, attacker tries the
  stolen row's token hash" rather than "attacker brute-forces a user-chosen
  secret". HMAC is constant-time-comparable and stateless; Argon2id's slow-
  by-design parameters add latency without buying anything for high-entropy
  inputs. The key is distinct from `JWT_SIGNING_KEY` so that leaking one
  secret doesn't let an attacker forge the other. Decision committed in #14
  (Google callback was the first place a refresh token gets minted) and
  inherited by #15 / #16 / #17.
- **Rotation.** Every `/api/auth/refresh` revokes the row in use
  (`revoked_at = now()`) and inserts a fresh one. The cookie is rewritten.
- **Reuse detection.** If a request arrives bearing a token whose row is
  already `revoked_at IS NOT NULL`, the route revokes **all** of that
  `user_id`'s refresh tokens and returns `401`. This is the theft response
  from RFC 0001 § Failure modes.
- **Logout.** Revokes the current row only (`revoked_at = now()`).
- **Cascade.** `ON DELETE CASCADE` on `user_id` — deleting a user (when the
  GDPR-deletion epic ships) removes their tokens automatically.

## Account linking

If a user signs in with provider B using a verified email already associated
with provider A's account, we **prompt for explicit linking** rather than
silently merging. The reasons:

- Apple's "Hide My Email" returns a relay address (`abc123@privaterelay.appleid.com`),
  so email-match is not sufficient evidence of same-person.
- Facebook may return a different email per app (rare but possible).
- Silent merging on email is a documented account-takeover vector.

The link flow:
1. User signs in with B; we detect the existing A account on email match.
2. We hold session B in a short-lived "pending link" state.
3. User must confirm in the UI by re-authenticating with provider A.
4. Only then do we update the existing user row to support both providers
   (via a separate `user_identities` table — added in the auth PR).

### Detection vs. resolution

Detection lives in **each provider's callback** (`#14` Google, `#15` Apple,
`#16` Facebook). When a callback finds an existing different-provider user
with the same verified email, it returns the `link_required` envelope per the
OpenAPI `Session` schema — no `users` row inserted, no `refresh_tokens` row
written, no session cookie set. Detection requires a verified email on **both**
sides; an unverified email on the incoming token is treated as a fresh
unrelated identity (otherwise an attacker could claim arbitrary emails).

The collision response carries a short-lived `link_token`. **Storage choice:
the `link_token` is a stateless signed JWT** (HS256 with `JWT_SIGNING_KEY`,
`typ=link`, 10-minute TTL by default). It carries the existing user's id, the
second provider, the second-provider `sub`, the verified email, and a unique
`jti` (uuid4 hex). No server-side state to clean up; revocation isn't a
concern at this TTL. Alternatives considered: a Redis short-TTL entry
(rejected — adds infra dependency to the auth path; Redis isn't yet wired
into the app layer beyond health checks), a `link_intents` table (rejected —
durable but overkill for a self-resolving 5–10 minute flow that would also
need a sweeper).

**Single-use enforcement.** Without a `jti`, a leaked `link_token` would be
replayable for the full TTL. The `jti` claim is added at issue time in this
PR (#14); the **consumer** (#18, `POST /api/auth/link`) is responsible for
recording each consumed `jti` in `consumed_link_tokens` (or short-TTL Redis
SETEX keyed on `jti`) and rejecting replays. Decoding via
`app.auth.link.decode_link_token` exposes the `jti` on `LinkTokenClaims`
specifically so the consumer can do this; verification itself doesn't
enforce single-use because that requires storage that doesn't exist yet.

Resolution lives in **#18** (`POST /api/auth/link`), which validates the
`link_token`, requires fresh re-authentication with the original provider,
and merges identities.

### Google specifics

- **JWKS:** `https://www.googleapis.com/oauth2/v3/certs`, cached in-process for
  1 hour. JWKS unreachable → 503 (client-retry-friendly per RFC 0001).
- **Issuer claim:** `accounts.google.com` or `https://accounts.google.com`
  (Google issues both forms; both are accepted).
- **Audience claim:** must equal `GOOGLE_CLIENT_ID` exactly (also accepts the
  list form `aud: [client_id, ...]`).
- **Display-name fallback:** if the token omits `name`, we use `email`; if
  both are missing, the literal string `"ThreadLoop user"`.
- **`email_verified` normalization:** Google occasionally serializes this as
  the string `"true"`/`"false"`; the verifier normalizes both forms.
- **Unconfigured `GOOGLE_CLIENT_ID`:** the verifier raises rather than
  silently accepting any well-formed token. Misconfigured deploys fail loudly.

## Buyer/seller dual role

One `users` row per person. Two capability flags govern actions:

- `can_purchase` — gated on a verified email or phone. Default `true` for new
  accounts (most users start as buyers).
- `can_sell` — gated on completing seller onboarding (payout method,
  identity check). Default `false`; users opt in.

The `transactions` table references `buyer_id` and `seller_id` from the same
`users` table, with a `CHECK (buyer_id <> seller_id)` constraint preventing
self-purchase.

Authorization is done **per action**, not per account-type:

```python
@router.post("/listings")
def create_listing(user: User = Depends(require_seller)):  # checks can_sell
    ...

@router.post("/transactions")
def open_transaction(user: User = Depends(require_buyer)):  # checks can_purchase
    ...
```

A user can hold both roles simultaneously and switch contexts without
re-authenticating.

## What's not implemented yet

The scaffold has the schema and the abstract design. Wiring lands in
`feat/auth-sso` (Epic #11):
- Provider SDK integration (web + mobile)
- `/api/auth/apple/callback` (#15) and `/api/auth/facebook/callback` (#16)
- `/api/auth/refresh` + `/api/auth/logout` + `/api/me` + session middleware (#17)
- Account-linking *resolution* — `POST /api/auth/link` (#18). Detection is
  already wired into the Google callback (this PR).
- `require_buyer` / `require_seller` dependencies

Already landed:
- OpenAPI + TS contract for the auth endpoints (#12, PR #26).
- `refresh_tokens` table + `RefreshToken` model with rotation/expiry/revocation
  helpers (#22, PR #29).
- `POST /api/auth/google/callback`, the session helpers
  (`backend/app/auth/session.py`) #15/#16/#17 will reuse, the Google JWKS
  verifier with in-process caching, the HMAC-SHA-256 refresh-token hash, and
  cross-provider link-required detection (#14, this PR).

Until the remaining callback routes ship, `users` rows for Apple / Facebook
can still be inserted via Alembic seed data for local development.
