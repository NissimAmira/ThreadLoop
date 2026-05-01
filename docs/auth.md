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
`GOOGLE_CLIENT_ID`, `JWT_SIGNING_KEY`, `REFRESH_TOKEN_HMAC_KEY`,
`APPLE_CLIENT_ID`, `APPLE_TEAM_ID`, `APPLE_KEY_ID`, `APPLE_PRIVATE_KEY`,
`FACEBOOK_APP_ID`, and `FACEBOOK_APP_SECRET` are set non-empty. This catches
the common misconfiguration where an unset provider secret would silently
make every sign-in look like "your token is invalid" (401) when the real
fault is server config.

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

### Apple specifics

- **JWKS:** `https://appleid.apple.com/auth/keys`, cached in-process for 1
  hour. JWKS unreachable → 503. Same invalidate-and-retry-once rotation
  handler as Google, since Apple also rotates signing keys on a multi-day
  cadence.
- **Issuer claim:** must equal `https://appleid.apple.com` exactly.
- **Audience claim:** must equal `APPLE_CLIENT_ID` (the **Service ID** from
  the Apple Developer portal, not the Team ID). Accepts list form too.
- **`is_private_email` (Hide-My-Email) bypass.** When the ID token carries
  `is_private_email: true`, the `email` claim is a per-app relay address
  (`*@privaterelay.appleid.com`). Matching that against existing rows would
  never legitimately succeed — and worse, would let an attacker who created
  a relay address provoke the link flow against random verified-email
  accounts. The Apple callback **skips the cross-provider collision check
  entirely** on relay addresses and treats the sign-in as a fresh identity.
  Tested explicitly in `test_apple_relay_bypasses_link_required`.
- **Name only on first sign-in.** Apple includes `name` in its JS / native
  callback payload only on the very first authentication of a session —
  and only when the app requested the `name` scope. The client passes it
  in the `name` body field of `POST /api/auth/apple/callback` (optional);
  the backend uses it to seed `display_name` on a freshly-created user. On
  subsequent sign-ins the existing row's `display_name` is reused — we
  never overwrite from a missing-name token.
- **Display-name fallback:** if `name` is absent on a first sign-in, we use
  `email` if present, then literal `"ThreadLoop user"` (mirrors Google's
  fallback).
- **`email_verified` and `is_private_email` normalization:** Apple sends
  these as either booleans or the strings `"true"`/`"false"`; the verifier
  normalizes both forms.
- **`code` field on the request.** Required by the OpenAPI contract but not
  exchanged in this PR. Apple's `code` exchange at
  `appleid.apple.com/auth/token` would only matter if we wanted Apple-side
  refresh tokens; our refresh-token lifecycle lives in `refresh_tokens` and
  the ID token alone is sufficient to establish identity. The
  `client_secret` JWT generator (see below) is exposed for a future job
  without being on the hot path of this callback.
- **`client_secret` is itself a JWT.** Apple's token endpoint expects the
  `client_secret` parameter to be an ES256-signed JWT, not a static string.
  Claims:
  - `iss` = `APPLE_TEAM_ID` (10-character team identifier from the
    Apple Developer portal Membership page).
  - `iat` = now.
  - `exp` = now + 1 hour. (Apple permits up to 6 months; we keep it short
    so a leaked `.p8` only buys an attacker 1 hour and so manual rotation
    propagates within a process restart.)
  - `aud` = `https://appleid.apple.com`.
  - `sub` = `APPLE_CLIENT_ID` (the Service ID).
  - Header `alg` = `ES256`, `kid` = `APPLE_KEY_ID`.

  Signed with the contents of the `.p8` key downloaded from the Apple
  Developer portal → Keys, and stored in `APPLE_PRIVATE_KEY` as multi-line
  PEM. The signed JWT is cached in-process for 50 minutes (under the 1-hour
  `exp`) so we don't resign per request.
- **Deferred `client_secret` rotation.** RFC 0001 § Risks tracks the open
  question of a scheduled `.p8` rotation job. We've deferred it: rotation
  cadence is "manually rotate the `.p8` and bounce the process" for now,
  which the 50-minute in-process cache window naturally accommodates. A
  scheduled job becomes worthwhile when we're running enough replicas that
  bouncing the fleet for rotation is operationally awkward.
- **Unconfigured Apple secrets:** as with Google, the verifier raises rather
  than silently accepting any well-formed token; the `client_secret`
  signing helper raises rather than producing an unsigned JWT.

### Facebook specifics

Facebook is the odd one out: the OAuth flow surfaces a **user access token**,
not an OIDC ID token. There is no JWT signature to verify against a JWKS;
the security guarantee comes from two server-side calls to the Graph API.

- **Two Graph API calls per sign-in.**
  1. `GET https://graph.facebook.com/debug_token` with
     `input_token=<user_access_token>` and
     `access_token=<app_access_token>`. Validates the user token is current
     (not expired, not revoked) AND that it was issued for **our** Facebook
     app (`data.app_id == FACEBOOK_APP_ID`).
  2. `GET https://graph.facebook.com/me?fields=id,name,email,picture` with
     `Authorization: Bearer <user_access_token>`. Returns the stable
     `(provider='facebook', provider_user_id=id)` identity plus optional
     name, email, and avatar URL.
- **Why `/debug_token` first.** `/me` alone is user-scoped — it would happily
  return a user profile to whoever holds the access token, including a
  malicious Facebook app that obtained the token via a separate sign-in
  flow. `/debug_token`'s `app_id` check is the only Graph-side mechanism
  that asserts "this token was issued for THIS app". Cost is one extra
  HTTP call inside the same `httpx.Client`. Decision committed in #16.
- **App access token construction.** Per Meta's docs, the app access token
  required by `/debug_token` is the literal string
  `"{FACEBOOK_APP_ID}|{FACEBOOK_APP_SECRET}"` — no Graph round-trip needed
  to obtain it. We rebuild it per verification rather than caching, so the
  process never holds a long-lived secret-shaped value beyond the request.
- **No JWT, no JWKS, no key cache.** The Graph calls are the trust anchor.
  This means there is no rotation handler analogous to the Google / Apple
  invalidate-and-retry-once path — Facebook key rotation is invisible to us.
- **Email permission is optional.** The `email` permission is a separately
  granted scope; users can decline it in Facebook's consent dialog, in which
  case `/me` omits the `email` field. The verifier surfaces `email=None`
  and `email_verified=False`, the route's display-name fallback chain is
  `name → email → "ThreadLoop user"`, and the cross-provider collision
  check trivially can't fire (no email to match).
- **`email_verified` is always `False`.** The Graph API does **not** expose
  a verified-email flag on `/me`. Treating any returned email as unverified
  is the deliberate choice — silently auto-merging on an unverified email
  would be the same account-takeover vector the Google and Apple branches
  already guard against. Result: the cross-provider collision detection
  (which requires verified emails on both sides) **never fires for
  Facebook sign-ins**. The conditional is kept verbatim in the route layer
  so a future change to Facebook's Graph response (e.g. adding a `verified`
  flag) plugs in cleanly. Account merging across `Facebook ↔ Google/Apple`
  is therefore exclusively user-initiated through the linking flow shipping
  in #18.
- **No relay-equivalent.** Apple's `is_private_email` bypass exists because
  Apple's own ID token tells us the email is a relay address; Facebook has
  no analogous signal because it never claims verification in the first
  place. The collision check is the standard one (and never fires per
  above).
- **Failure mapping.** `/debug_token` 5xx or transport-level
  unreachability → `503 graph_api_unavailable`. `/debug_token` 4xx, token
  reported as invalid, token issued for a different app, malformed Graph
  response, or `/me` 401 → `401 invalid_token`. The route never echoes the
  upstream verifier message — it can carry token contents.
- **Unconfigured Facebook secrets.** As with Google and Apple, the verifier
  raises rather than silently accepting any token; `Settings()` refuses to
  construct with `auth_enabled=True` and an empty `FACEBOOK_APP_ID` or
  `FACEBOOK_APP_SECRET`.

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
- `/api/auth/refresh` + `/api/auth/logout` + `/api/me` + session middleware (#17)
- Account-linking *resolution* — `POST /api/auth/link` (#18). Detection is
  already wired into the Google and Apple callbacks; Facebook never trips
  the link path because Graph API doesn't expose `email_verified` (see
  Facebook specifics).
- `require_buyer` / `require_seller` dependencies
- Scheduled `client_secret` JWT rotation job (RFC 0001 § Risks).

Already landed:
- OpenAPI + TS contract for the auth endpoints (#12, PR #26).
- `refresh_tokens` table + `RefreshToken` model with rotation/expiry/revocation
  helpers (#22, PR #29).
- `POST /api/auth/google/callback`, the session helpers
  (`backend/app/auth/session.py`) every callback reuses, the Google JWKS
  verifier with in-process caching, the HMAC-SHA-256 refresh-token hash, and
  cross-provider link-required detection (#14, PR #31).
- `POST /api/auth/apple/callback`, the Apple JWKS verifier with the same
  invalidate-and-retry-once rotation handler, the ES256 `client_secret`
  JWT generator with 50-minute in-process cache, the Hide-My-Email relay
  bypass for cross-provider collision detection, and the name-only-on-
  first-signin display-name handling (#15, PR #33).
- `POST /api/auth/facebook/callback`, the Graph-API-backed verifier with
  `/debug_token` validation against `FACEBOOK_APP_ID` followed by `/me`
  for the profile, and the design choice to treat every Facebook email as
  unverified (so the cross-provider collision check never fires for
  Facebook) (#16, this PR).
