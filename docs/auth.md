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
`/api/auth/*` route AND `/api/me` return 404 — the implementation is in the
binary but unreachable. This lets us land each provider, the refresh /
logout / `/me` work, and account-linking incrementally without exposing
half-built flows.

The flag is enforced as a router-level FastAPI dependency
(`require_auth_enabled`, exported from `app/auth/deps.py` and applied to
both the auth router and the users router), not by conditionally
registering routers, so OpenAPI generation stays honest — the routes still
appear in `/docs` and the contract doesn't lie about what the deployed
binary will look like once the flag is flipped. Both surfaces are gated
identically: `/api/me` 404s under flag-off in lockstep with `/api/auth/*`
so a probe can't tell the auth subsystem exists from the response.

### Per-provider gating — `GOOGLE_ENABLED` / `APPLE_ENABLED` / `FACEBOOK_ENABLED`

The master `AUTH_ENABLED` flag turns the subsystem on; three per-provider
boolean flags decide which providers' callbacks are reachable within an
auth-enabled deployment. Default is `false` for all three, mirroring
Epic #11's slice-by-slice rollout: slice 1 ships Google end-to-end, slice 2
broadens to Apple, slice 3 to Facebook. The split exists because the
previous validator forced operators running a Google-only slice 1 demo to
stuff dummy values into Apple and Facebook env vars just to boot — at
which point the validator no longer caught the misconfiguration it was
designed to (issue #51).

Behaviour matrix:

| `AUTH_ENABLED` | `<PROVIDER>_ENABLED` | `POST /api/auth/<provider>/callback` |
| --- | --- | --- |
| `false` | (any) | 404 (master gate) |
| `true` | `false` | 404 (per-provider gate) |
| `true` | `true` | runs |

Both 404s carry the bare FastAPI `{"detail": "Not Found"}` envelope, so a
probe can't distinguish the master flag-off state from a per-provider
flag-off state. Per-provider gating runs **before** body validation in
the dispatcher: a 422 for a malformed body of a disabled provider would
leak the contract surface, so the disabled-provider 404 wins.

> **Adding a fourth provider** requires updating three places in lockstep,
> in the same commit: the `Literal[...]` type on the dispatcher's path
> parameter in `app/routers/auth.py`, the `_KNOWN_PROVIDERS` frozenset in
> the same file, and a new `<provider>_enabled` flag on `Settings` (with a
> matching entry in `_PROVIDER_FLAG_ATTR` in `app/auth/deps.py`). Half-
> adding a provider — e.g. landing the Settings flag without the dispatcher
> Literal — produces a routing surface that disagrees with what the gate
> actually checks.

`/api/me`, `/api/auth/refresh`, and `/api/auth/logout` are provider-
agnostic — they're gated only by the master `AUTH_ENABLED` flag.

When `AUTH_ENABLED=true`, `Settings()` refuses to construct unless the
**cross-cutting** secrets `JWT_SIGNING_KEY` and `REFRESH_TOKEN_HMAC_KEY`
are set non-empty (every provider's session helpers reach for them). For
each per-provider flag set to `true`, the validator additionally requires
that provider's secrets:

- `GOOGLE_ENABLED=true` → `GOOGLE_CLIENT_ID`.
- `APPLE_ENABLED=true` → `APPLE_CLIENT_ID`, `APPLE_TEAM_ID`,
  `APPLE_KEY_ID`, `APPLE_PRIVATE_KEY`.
- `FACEBOOK_ENABLED=true` → `FACEBOOK_APP_ID`, `FACEBOOK_APP_SECRET`.

The web client takes the matching set of `VITE_*` env vars: each provider
slice that's enabled in a build needs its own client-side identifier so
the SDK can bootstrap. Mismatched FE/BE values (e.g. a Service ID on the
FE that disagrees with the BE's `APPLE_CLIENT_ID`) silently fail
verification at the JWKS step — the BE's `aud` check rejects the token —
so the deploy story keeps them in lockstep:

- `GOOGLE_ENABLED=true` → set `VITE_GOOGLE_CLIENT_ID` (web build) to the
  same Google project as `GOOGLE_CLIENT_ID` (backend).
- `APPLE_ENABLED=true` → set `VITE_APPLE_CLIENT_ID` (web build) to the
  same Service ID as `APPLE_CLIENT_ID` (backend).
  `VITE_APPLE_REDIRECT_URI` is optional; defaults to
  `window.location.origin` if unset.
- `FACEBOOK_ENABLED=true` → (slice 3, not yet shipped) will pair a
  `VITE_FACEBOOK_APP_ID` with `FACEBOOK_APP_ID`.

When the FE env var is missing for a provider whose BE flag is on, the
sign-in page renders that provider's button disabled with no scary error
— it's the same actionable-misconfiguration UX as Google's
"VITE_GOOGLE_CLIENT_ID is not set" path. The other enabled providers
still work.

The validator catches the misconfiguration where an unset provider secret
would silently make every sign-in look like "your token is invalid" (401)
when the real fault is server config. Per-provider gating preserves the
loud-fail property for whichever providers ARE enabled, and lets a
slice-1 demo boot with only `AUTH_ENABLED=true` + `GOOGLE_ENABLED=true`
+ the cross-cutting secrets + `GOOGLE_CLIENT_ID` set.

Settings is loaded at boot — there is no runtime hot-toggle. Flipping a
provider on or off requires a process restart, matching the master flag's
semantics.

Rollout sequence (RFC 0001):
1. Implementation lands flag-off.
2. Web sign-in page lands flag-off.
3. Flag flipped on in **staging** (Phase 2 of the DevOps roadmap).
4. Mobile sign-in lands flag-off.
5. Flag flipped on in **prod** once all three platforms validate in staging.

Per-provider flags follow the same staging-before-prod cadence: slice N's
`<PROVIDER>_ENABLED=true` lands in staging first, gets validated, then
flips in production.

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
  Implementation (#17, slice 1): the route reads the `refresh_token` cookie,
  HMAC-hashes it, looks up the row. On a happy match we set the existing
  row's `revoked_at`, mint a fresh `(plaintext, row)` pair via
  `mint_refresh_token`, set the new cookie, mint a new access JWT, and
  commit the unit of work. Failures (no cookie / unknown hash / expired /
  revoked / orphaned user) all 401 with the `invalid_refresh_token` code
  and clear the cookie on the way out so a stale value doesn't keep
  replaying.
- **Reuse detection.** If a request arrives bearing a token whose row is
  already `revoked_at IS NOT NULL`, the route revokes **all** of that
  `user_id`'s refresh tokens (one `UPDATE ... WHERE revoked_at IS NULL`)
  and returns `401`. This is the theft response from RFC 0001 § Failure
  modes: we can't distinguish a benign replay (e.g. a stale tab) from
  active token theft, so we burn the entire refresh-token surface and
  force re-auth. Logged at WARNING level with `user_id`, the row's
  `issued_at`, and the age delta — so ops can distinguish a benign
  back-button replay (small delta) from a stale token revived weeks
  later (large delta = real theft signal). Tested in
  `test_refresh_route.py::test_refresh_with_revoked_token_triggers_reuse_detection`.
- **Quiet failure paths log differentiated reasons.** The other three
  401 paths (`hash_not_found`, `token_expired`, `user_not_found`) emit
  `INFO` lines tagged with the reason so ops can grep them apart. The
  log lines never carry the cookie value, the cookie's hash, or any
  other client-controlled data — `user_id` is included only when the
  row actually exists.
- **Logout.** Revokes the current row only (`revoked_at = now()`).
  Idempotent — a missing/unknown/already-revoked cookie still returns 204.
  The Set-Cookie clear is unconditional. The route accepts no body.
- **Cascade.** `ON DELETE CASCADE` on `user_id` — deleting a user (when the
  GDPR-deletion epic ships) removes their tokens automatically.

### Bearer-JWT validation — `require_user`

`app.auth.deps.require_user` is the FastAPI dependency every protected
route uses to resolve the bearer access JWT into a `User` row. Single
failure envelope (401 with the OpenAPI `Error` shape):

```python
from app.auth.deps import require_user

@router.get("/me")
def me(user: User = Depends(require_user)) -> UserOut: ...
```

Rejects (all → 401):

- Missing `Authorization` header → `not_authenticated`.
- Header present but not `Bearer <token>` → `invalid_authorization_scheme`.
- JWT signature / expiry / structural failure → `invalid_token`. The dep
  collapses authlib's various JoseError subtypes into one envelope so the
  response doesn't leak which check failed.
- `typ` claim missing or not `"access"` → `invalid_token`. The link
  tokens (`typ=link`) are signed with the same `JWT_SIGNING_KEY` and
  would otherwise pass JOSE verification; the `typ` discriminator keeps
  them apart.
- `sub` claim missing or not a UUID → `invalid_token`.
- User row not found (account deleted between issue and use) →
  `invalid_token`. Same envelope to avoid leaking "this user used to
  exist" to a probe.

**Access-token claims:** `sub` (user id), `iat`, `exp`, `typ=access`,
and `jti` (a fresh `uuid4().hex` per mint). `jti` is included so two
consecutive mints in the same wall-clock second produce byte-distinct
JWTs — without it, deterministic HS256 over identical claims yields the
same encoding and rotation across the refresh boundary becomes
unobservable. `require_user` ignores the claim today; it's the
foundation for any future server-side denylist (RFC 0001 § Risks
explicitly defers a JWT denylist).

**Role gates (`require_seller`/`require_buyer`) are NOT in this module.**
They're deferred to issue #37 per the 2026-05-01 vertical-slicing pivot
and ship with their first consumers in the listings/transactions epics.
This keeps the dep surface honest about what's actually exercised.

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
  - **The exemption is bidirectional.** An existing Facebook row also won't
    trigger `link_required` on an incoming Google or Apple sign-in, because
    both branches require `existing.email_verified=true` to consider a row
    a collision candidate. Net effect: a user who signs up with Facebook
    first and Google second gets two unrelated accounts with no link prompt
    in either direction. This is defensible — we don't trust Facebook's
    email at all, in either role — but it means Epic #11's AC ("Account-
    linking prompt fires when an email collision is detected across
    providers") is fully exempt for Facebook identities. Cross-provider
    linking that involves a Facebook account is exclusively user-initiated
    through #18.
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
  construct with `facebook_enabled=True` and an empty `FACEBOOK_APP_ID` or
  `FACEBOOK_APP_SECRET`. (Under `facebook_enabled=False`, Facebook secrets
  are optional and the callback returns 404.)

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

## Web client (slices 1 & 2 — Google + Apple)

The web sign-in surface ships in vertical slices (#19, #38, #39, #40). Slice
1 lands the Google end-to-end demo plus the shared scaffolding all later
slices reuse; slice 2 (#38) adds the Apple button next to it.

### Auth context

`frontend-web/src/auth/AuthContext.tsx` exposes a single `useAuth()` hook
returning a tagged-union `state` and two actions:

```ts
type AuthState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "authenticated"; user: User; accessToken: string };

interface AuthContextValue {
  state: AuthState;
  signIn: (session: AuthenticatedSession) => void;
  signOut: () => Promise<void>;
}
```

Three states only — kept deliberately small so consumers don't write boolean
ladders. `loading` is the gap before the first-paint silent refresh resolves;
`anonymous` is steady-state-no-session; `authenticated` carries both the user
and the access JWT so consumers don't need a second hook to make an
authenticated request.

The provider mounts a single `useEffect` on first render that calls
`POST /api/auth/refresh`. If the refresh cookie is valid the user lands on
`authenticated`; any failure (401, network) collapses to `anonymous`. The
in-memory access token never touches `localStorage` per RFC 0001's
"in-memory only" stance.

`signIn` accepts an `AuthenticatedSession` directly (the Google callback
return shape). `signOut` posts `/api/auth/logout` and drops to `anonymous`
even if the network call fails — the route is idempotent server-side.

### Google Identity Services

`frontend-web/src/auth/google.ts` lazy-loads
`https://accounts.google.com/gsi/client` on first need and exposes a typed
`loadGoogleIdentity()` promise. Tests + Cypress install
`window.__threadloopGoogleIdStub__` before the page mounts; the loader
returns the stub instead of injecting the real script, which keeps the
smoke test deterministic and removes any need for a real OAuth client id in
CI. `VITE_GOOGLE_CLIENT_ID` is required at runtime in real builds — when
unset, `/sign-in` renders an actionable configuration error rather than a
silently broken button.

### Sign in with Apple JS

`frontend-web/src/auth/apple.ts` mirrors the Google loader: it lazy-loads
`https://appleid.cdn-apple.com/appleauth/static/jsapi/appleid/1/en_US/appleid.auth.js`
on first need and exposes a typed `loadAppleIdentity()` promise. Tests +
Cypress install `window.__threadloopAppleIdStub__` before mount, same as
the Google stub seam.

The page renders its own Tailwind-styled button rather than Apple's
declarative `<div id="appleid-signin">` — the declarative widget requires
the SDK script to be loaded before the markup paints, which fights React's
render order and made the Apple init effect race with first paint.
Rendering our own button with the brand-mark SVG and calling
`AppleID.auth.signIn()` on click is the path of least surprise inside React
and matches Apple's brand guidelines (black bg / white logo / white
"Sign in with Apple" text).

`AppleID.auth.signIn()` resolves with `{ authorization: { id_token, code,
state? }, user? }`. The page posts `{ idToken, code, name? }` to
`POST /api/auth/apple/callback`; `name` is the joined `firstName lastName`
from the response's `user` block, which Apple only ships on first sign-in
(and only when the app requested the `name` scope). Subsequent sign-ins
omit `user` entirely and the backend reuses the existing
`users.display_name`. `composeAppleDisplayName` collapses missing or
whitespace-only halves to `undefined` so the request body never carries
`name: ""`.

User-cancellation rejections (`{ error: "popup_closed_by_user" }`,
`{ error: "user_cancelled_authorize" }`) are swallowed without surfacing a
scary error — the user can just click again. Other rejection shapes
surface as a retryable "Could not start Apple sign-in" message.

`VITE_APPLE_CLIENT_ID` is required at runtime in real builds; when unset,
the Apple button renders disabled rather than launching a broken popup.
`VITE_APPLE_REDIRECT_URI` is optional and defaults to
`window.location.origin` — a same-origin configuration is the common case;
override only when the build is served from an origin different from the
one registered against the Apple Service ID.

`link_required` responses on the Apple branch surface the same generic
"This email is registered with another provider…" message as Google
(slice-4 / #40 will replace it with the full re-auth UI). Apple-relay
emails (`*@privaterelay.appleid.com`) flow through unchanged — the
backend's `is_private_email` bypass means the relay account always lands
as a fresh identity, and the FE just renders whatever email the BE
returned on `/me`.

### Out of scope here

The Facebook sign-in button (#39) and the full `link_required` linking UI
(#40) ship in their own slices. Slices 1 and 2's `link_required` handling
is a generic error message ("This email is registered with another
provider; please sign in with that provider instead") with no second-step
re-auth — enough to validate the contract surface without prematurely
building UI that #40 will rework.

## What's not implemented yet

The scaffold has the schema and the abstract design. Wiring lands in
`feat/auth-sso` (Epic #11):
- Facebook sign-in button on web (slice 3 / #39).
- Full `link_required` linking UI flow on web (slice 4 / #40).
- Mobile SDK integration (#20).
- Account-linking *resolution* — `POST /api/auth/link` (#18). Detection is
  already wired into the Google and Apple callbacks; Facebook never trips
  the link path because Graph API doesn't expose `email_verified` (see
  Facebook specifics).
- `require_buyer` / `require_seller` dependencies (#37 — defer to listings
  / transactions epics where the first consumers land)
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
  Facebook) (#16).
- **Slice-1 BE half** (#17): `POST /api/auth/refresh` (rotation +
  reuse-detection), `POST /api/auth/logout` (idempotent), `GET /api/me`,
  and `app.auth.deps.require_user`. With this PR + the slice-1 FE half
  (#19) merged and `AUTH_ENABLED=true` set, the demo "click Google →
  see your name on /me → page refresh keeps the session → logout"
  works end-to-end. Apple `_ClientSecretCache` cache-key fix (item #1
  from #34) bundled in: cache now keys on
  `(team_id, client_id, key_id, hash(private_key_pem))` so a manual
  rotation no longer serves a stale-but-still-young JWT.
- **Slice-1 FE half** (#19): `/sign-in` page with a single Google button
  (Google Identity Services SDK), `/me` page rendering display name +
  email, `useAuth()` context with silent-refresh on first paint, header
  reflecting the signed-in user, and a Cypress smoke test that stubs the
  Google flow and asserts the user lands on `/me`. `link_required`
  responses surface as a generic error string — the linking UI itself is
  slice 4 (#40). Auth context conventions documented above under
  "Web client (slices 1 & 2 — Google + Apple)".
- **Slice-2 FE** (#38): Apple sign-in button on `/sign-in` next to the
  Google one, wired via the Sign in with Apple JS SDK. Posts `{ idToken,
  code, name? }` to `POST /api/auth/apple/callback`; on success follows
  the same redirect path as Google (`?next=` or `/`). `link_required`
  reuses the slice-1 generic-error path; the full link UI is still slice
  4. Apple-relay email accounts flow through end-to-end (backend's
  `is_private_email` bypass plus an FE that doesn't special-case email
  shapes). New env vars: `VITE_APPLE_CLIENT_ID` (required when
  `APPLE_ENABLED=true`) and `VITE_APPLE_REDIRECT_URI` (optional). Cypress
  smoke at `cypress/e2e/sign-in-apple.cy.ts`.
- **camelCase wire shape** (#44): the contract drift between
  `shared/openapi.yaml` (snake) and `shared/src/types/` (camel) inherited
  from #12 was resolved by flipping the wire to camelCase via Pydantic
  `alias_generator=to_camel + populate_by_name=True +
  serialize_by_alias=True` (ADR 0009). The per-endpoint adapter slice 1
  shipped in `frontend-web/src/api/client.ts` is retired; web (and mobile,
  when slice 5 lands) consume the typed shapes from `@threadloop/shared`
  directly with no boundary translation.
