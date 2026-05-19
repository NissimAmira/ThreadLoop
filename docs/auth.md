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
| -------------- | -------------------- | ------------------------------------ |
| `false`        | (any)                | 404 (master gate)                    |
| `true`         | `false`              | 404 (per-provider gate)              |
| `true`         | `true`               | runs                                 |

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

The web client takes the matching set of `VITE_*` env vars in two
categories: a per-provider **enable flag** that mirrors the BE flag, and
a per-provider **client ID** that the SDK uses to bootstrap. Mismatched
FE/BE values (e.g. a Service ID on the FE that disagrees with the BE's
`APPLE_CLIENT_ID`) silently fail verification at the JWKS step — the BE's
`aud` check rejects the token — so the deploy story keeps them in
lockstep:

- `GOOGLE_ENABLED=true` (BE) ↔ `VITE_GOOGLE_ENABLED=true` (FE) +
  `VITE_GOOGLE_CLIENT_ID` set to the same Google project as
  `GOOGLE_CLIENT_ID` (BE).
- `APPLE_ENABLED=true` (BE) ↔ `VITE_APPLE_ENABLED=true` (FE) +
  `VITE_APPLE_CLIENT_ID` set to the same Service ID as `APPLE_CLIENT_ID`
  (BE). `VITE_APPLE_REDIRECT_URI` is optional; defaults to
  `window.location.origin` if unset.
- `FACEBOOK_ENABLED=true` (BE) ↔ `VITE_FACEBOOK_ENABLED=true` (FE) +
  `VITE_FACEBOOK_APP_ID` set to the same Meta App ID as `FACEBOOK_APP_ID`
  (BE). The BE's `/debug_token` verifier checks `data.app_id` against
  that value.

The FE flags are an **explicit signal**, not a derived one. Earlier
slices coupled "is this provider live?" to "is the client ID present?",
which happened to coincide with reality but didn't scale — Apple's
descope (PR #58) forced the split: a stale `VITE_APPLE_CLIENT_ID`
in a local `.env` shouldn't accidentally re-enable a button the build
isn't meant to ship. Strict `=== "true"` parse on every flag (anything
else, including unset, is `false`).

Per-provider behaviour matrix (FE side, mirrors the BE table above):

| `VITE_*_ENABLED`   | `VITE_*_CLIENT_ID` | Mode  | Result                                                                                                               |
| ------------------ | ------------------ | ----- | -------------------------------------------------------------------------------------------------------------------- |
| `false` (or unset) | (any)              | (any) | Button hidden everywhere — flag wins                                                                                 |
| `true`             | set                | (any) | Button renders functional                                                                                            |
| `true`             | unset              | DEV   | Button renders, dev-targeted "not configured" error — preserves loud-misconfiguration semantics for active providers |
| `true`             | unset              | prod  | Button hidden — safe-prod fallback                                                                                   |

**Side-effect contract:** when `VITE_*_ENABLED=false`, that provider's
SDK script is never fetched and its global is never touched — the flag
short-circuits before any `loadGoogleIdentity` / `loadAppleIdentity`
call. Asserted by the `loadXIdentity not called` tests in
`SignInPage.test.tsx`. Don't move the flag check after the SDK load:
a flag-off build must not pull a third-party script over the wire.

Slice-1-only deployment example (the demo on main today): set
`AUTH_ENABLED=true` + `GOOGLE_ENABLED=true` on the backend with
`GOOGLE_CLIENT_ID` configured, and on the web build set
`VITE_GOOGLE_ENABLED=true` + `VITE_GOOGLE_CLIENT_ID=…` with
`VITE_APPLE_ENABLED=false` and `VITE_FACEBOOK_ENABLED=false`. The
Apple/Facebook secrets and client IDs can be left empty.

If **every** FE flag is `false` in a build (e.g. a misconfigured deploy
where no provider made it through), the page substitutes an empty-state
message — _"Sign-in is currently unavailable. Please try again later."_
— for the button slots so users don't read the page as broken. The
empty state also fires when every flag is `true` but no client IDs are
set in a prod build (each provider falls into the safe-prod hide path).

The flag-read happens inline inside `frontend-web/src/pages/SignInPage.tsx`
(see `readGoogleEnabled` / `readAppleEnabled` / `readFacebookEnabled`).
A future engineer searching for `APPLE_ENABLED` will find both halves:
the BE side in `backend/app/config.py` § `Settings.apple_enabled` and
the FE side in `SignInPage.tsx`.

> **Local dev migration note:** `.env` files copied from before PR #53
> don't carry the BE `*_ENABLED` flags and will boot the backend with the
> entire auth subsystem off; `.env` files copied from before this PR's
> FE-flag rollout don't carry `VITE_*_ENABLED` and will hide every
> provider button (every flag defaults to `false` under the strict
> `=== "true"` parse). Re-copy both `backend/.env.example` and
> `frontend-web/.env.example` and set at minimum `AUTH_ENABLED=true` +
> `GOOGLE_ENABLED=true` on the BE plus `VITE_GOOGLE_ENABLED=true` on
> the FE for the slice-1 demo to render its Google button.

> **`make dev` path:** the docker-compose stack reads root `.env` (not
> `frontend-web/.env`) and forwards a fixed list of vars into the `web`
> container's environment block — `frontend-web/.env` isn't mounted in
> container mode. The `VITE_*_ENABLED` flags must therefore live in
> **both** `frontend-web/.env.example` (raw `npm run dev` workflow) and
> the root `.env.example` + the `web:` service's `environment:` block in
> `infra/docker/docker-compose.yml` (the `make dev` workflow). When
> upgrading an existing local stack across this PR, append the three
> `VITE_*_ENABLED` lines to your root `.env` (or delete-and-recreate it
> from the updated `.env.example`) — otherwise the flags arrive at the
> web container as empty strings and the page renders the empty state.

The validator catches the misconfiguration where an unset provider secret
would silently make every sign-in look like "your token is invalid" (401)
when the real fault is server config. Per-provider gating preserves the
loud-fail property for whichever providers ARE enabled, and lets a
slice-1 demo boot with only `AUTH_ENABLED=true` + `GOOGLE_ENABLED=true`

- the cross-cutting secrets + `GOOGLE_CLIENT_ID` set.

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

| Provider | SDK (web)                | SDK (mobile)                             | Notes                                                                                |
| -------- | ------------------------ | ---------------------------------------- | ------------------------------------------------------------------------------------ |
| Google   | Google Identity Services | `expo-auth-session`                      | Standard OIDC.                                                                       |
| Apple    | Sign in with Apple JS    | `expo-apple-authentication` (iOS native) | `client_secret` is a JWT signed with the team key (rotates ~6mo).                    |
| Facebook | Facebook Login SDK       | `expo-auth-session`                      | Returns access token, not ID token — we exchange for the user profile via Graph API. |

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

-- Slice 4 (#18): all `(provider, provider_user_id)` tuples a user can
-- sign in with. The primary identity (mirroring users.(provider,
-- provider_user_id)) is backfilled by migration 0003; linked secondary
-- identities are inserted by the POST /api/auth/link route.
user_identities (
    id                  uuid primary key,
    user_id             uuid not null references users(id) on delete cascade,
    provider            text not null,
    provider_user_id    text not null,
    linked_at           timestamptz not null default now(),
    UNIQUE (provider, provider_user_id)
);
CREATE INDEX ix_user_identities_user_id ON user_identities(user_id);

-- Slice 4 (#18): single-use enforcement for link_token. Each consumed
-- jti is recorded here; replays return 401. No FK to users — the jti is
-- opaque to user identity and we don't want the table size bound to
-- user-deletion patterns.
consumed_link_tokens (
    jti          varchar(64) primary key,
    consumed_at  timestamptz not null default now(),
    expires_at   timestamptz not null                  -- copied from link_token.exp; future sweeper drops by this
);
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
2. We hold session B in a short-lived "pending link" state — no `users` /
   `refresh_tokens` row written, no cookie set; client gets a signed JWT
   `link_token` to carry through the next step.
3. User must confirm in the UI by re-authenticating with provider A
   (the original provider's button is highlighted on the link screen).
4. Client posts `{linkToken, originalProvider, credential}` to
   `POST /api/auth/link`. Backend validates the link_token, re-verifies
   the credential via A's verifier, asserts the resulting identity is the
   existing user's primary identity, then adds the second-provider identity
   to that user's `user_identities` rows and mints a fresh session.

### Data model — `user_identities` (slice 4 / #18)

Identity storage is split across two surfaces:

- `users.(provider, provider_user_id)` — the **primary** identity (the
  original provider this user signed up with). Preserved unchanged across
  linking — the wire field `User.provider` (singular) continues to point
  at the original provider after a merge. This keeps the contract surface
  stable and avoids a `User` schema bump in this Epic; a future
  "manage linked accounts" Epic can add `GET /api/me/identities` or
  upgrade `User.provider` to a list without breaking today's consumers.
- `user_identities.(provider, provider_user_id, user_id)` — the **source
  of truth** for "all identities this user can sign in with." Holds one
  row per provider identity, including the primary. Callbacks look users
  up via this table (`app.auth.identity.find_user_by_identity`) rather
  than scanning `users.(provider, provider_user_id)` directly. The
  `(provider, provider_user_id)` unique constraint prevents two users
  from claiming the same provider identity. Cascade-on-user-delete
  mirrors `refresh_tokens`.

Migration 0003 backfills the primary identity into `user_identities` for
every existing user, so the lookup-via-identities path returns the same
answer for first-time / single-provider users; it additionally returns the
right user when signing in via a linked secondary provider.

The slice-4 `[backend-dev pushback]` comment on #18 documents the three
options considered for `User.provider` (keep singular as primary;
`providers: AuthProvider[]`; separate `/api/me/identities` endpoint) and
the rationale for picking option 1.

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
replayable for the full TTL. The `jti` claim is added at issue time in #14;
the consumer (#18, `POST /api/auth/link`) records each consumed `jti` in the
`consumed_link_tokens` table and rejects replays with 401. Storage choice
landed as a dedicated table (over Redis SETEX) per the slice-4 dispatch
recommendation on #11 — Redis isn't yet wired into the auth path beyond
health checks. The table is small (jti PK + two timestamps); cleanup of
expired rows is a future concern (sweeper job or periodic
`DELETE WHERE expires_at < now()`).

### Resolution — `POST /api/auth/link` (slice 4 / #18)

The consumer endpoint shipping the slice-4 BE half. Request shape:

```json
{
  "linkToken": "<jwt from link_required envelope>",
  "originalProvider": "google",
  "credential": { "idToken": "..." }
}
```

`credential` is the same provider-specific shape the original
`POST /api/auth/{provider}/callback` accepts (one of
`GoogleSsoCallbackInput` / `AppleSsoCallbackInput` /
`FacebookSsoCallbackInput`); the backend re-uses the original provider's
verifier to validate it.

**Why "fresh provider-A credential" over the alternatives.** Three
designs were considered for the `second_provider_session_proof` field:
(a) a fresh provider-A credential, (b) a server-side ephemeral session id,
(c) re-running the full provider-A callback first and passing the resulting
access JWT. (a) keeps the route stateless, re-uses existing verifier paths,
and survives the SDK round-trip naturally on the FE side. (b) would add
new ephemeral state for a one-shot flow. (c) would mint a session that gets
discarded immediately and leave a stranded refresh-token row from the
throwaway sign-in. (a) is the path of least surprise; documented inline in
the route docstring.

**Failure mapping** (single 401 envelope on every link-token failure so a
probe can't distinguish "no such jti" from "wrong original provider" from
"credential failed verification"):

- 401 `invalid_link_token` — link token signature/typ/expiry/structural
  failure, replay (jti already consumed), `originalProvider` mismatch,
  existing user no longer exists, credential failed verification, or
  credential resolves to a different user than the link token's target.
- 409 `identity_already_linked` — the second-provider identity is already
  claimed by a _different_ user. Distinguished from the 401 attack-signal
  cases because this is a genuine merge conflict (e.g. both accounts were
  created independently); the FE should surface it and route to support.
- 503 `jwks_unavailable` / `graph_api_unavailable` — original provider's
  JWKS or Graph API unreachable; client retries.
- 404 — auth subsystem disabled (`AUTH_ENABLED=false`), same as every
  other `/api/auth/*` route.

**Side effects on success:**

- New `user_identities` row for `(linkToken.new_provider,
linkToken.new_provider_user_id, user_id=existing user)`.
- `consumed_link_tokens` row for `linkToken.jti`.
- All outstanding refresh tokens for the existing user are revoked (linking
  is a high-trust state change; treat it the way a password rotation would
  be treated in a password world).
- Fresh access JWT + refresh token via `issue_session` — the response is
  the standard `Session` envelope with `linkRequired: false`.

`users.provider` / `users.provider_user_id` are NOT modified — the primary
identity is preserved across linking.

**Idempotency on repeated linking attempts.** If the user accidentally
runs the link flow twice with two distinct link_tokens (each with its own
`jti`, so neither is a replay), the route detects that the
`(claims.new_provider, claims.new_provider_user_id)` identity already
belongs to the same user and skips the `user_identities` insert. The
unique constraint on `(provider, provider_user_id)` is what keeps this
honest — without it, concurrent link attempts could quietly create
duplicate rows. Tested in
`test_link_route_integration.py::test_repeated_link_with_fresh_jti_idempotent`.

**Apple-relay bypass honored end-to-end.** The Apple callback's
`is_private_email` short-circuit (#15) means a relay sign-in NEVER
returns a `link_required` envelope, so the link route is unreachable
for relay addresses. This is the intended end-to-end behaviour: matching
on a per-app relay would be spurious, and would let an attacker who
created a relay address provoke the link flow against any account. Tested
explicitly in
`test_link_route_integration.py::test_apple_relay_never_reaches_link_route`.

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

## Web client (slices 1, 2, 3 & 4 — Google + Apple + Facebook + linking)

The web sign-in surface ships in vertical slices (#19, #38, #39, #40). Slice
1 lands the Google end-to-end demo plus the shared scaffolding all later
slices reuse; slice 2 (#38) adds the Apple button next to it; slice 3 (#39)
adds the Facebook button; slice 4 (#40) wires the cross-provider
account-linking modal on top.

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
Rendering our own button and calling `AppleID.auth.signIn()` on click is
the path of least surprise inside React. The button approximates Apple's
brand guidelines (black background, white logo, white "Sign in with Apple"
label, full SDK round-trip); the inline glyph is the Bootstrap Icons
`bi-apple` mark rather than Apple's official brand-asset SVG. Pre-App
Store-review (mobile slice / #20) we'll swap in the official mark from
Apple's brand-assets pack — for the web demo the approximate mark is
enough to validate the flow.

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

`link_required` responses on the Apple branch are intercepted by the
slice-4 `LinkAccountsDialog` (see § Link UI (slice 4 / #40)). Apple-relay
emails (`*@privaterelay.appleid.com`) flow through unchanged — the
backend's `is_private_email` bypass means the relay account always lands
as a fresh identity, and the FE just renders whatever email the BE
returned on `/me`.

### Facebook Login SDK

`frontend-web/src/auth/facebook.ts` mirrors the Google/Apple loaders: it
lazy-loads `https://connect.facebook.net/en_US/sdk.js` on first need and
exposes a typed `loadFacebookIdentity()` promise. Tests + Cypress install
`window.__threadloopFacebookIdStub__` before mount, same stub seam as the
Google + Apple SDKs.

The page renders its own Tailwind-styled button (white "f" glyph + white
"Continue with Facebook" text on `#1877F2` — Meta's own preferred wording
per their brand guidelines, which is intentionally different from
"Sign in with Apple" / "Sign in with Google" because each provider's
brand guideline mandates a slightly different verb). The brand colour is
extended into the Tailwind theme as `bg-facebook` / `bg-facebook-dark` /
`focus:ring-facebook` rather than inlined as a hex literal — same
discipline the rest of the codebase keeps for theme tokens.

`FB.login(cb, { scope: "email" })` resolves with
`{ status, authResponse: { accessToken, ... } | null }`. The page posts
`{ accessToken }` to `POST /api/auth/facebook/callback`; the BE re-validates
against Graph API (`/debug_token` then `/me`). A `status` other than
`"connected"` (user closed the popup, denied the app, network blip) is
treated as a silent cancel — error region stays empty, button reverts to
clickable. Mirrors Apple's user-cancel posture.

`VITE_FACEBOOK_APP_ID` is required at runtime in real builds; when unset,
the Facebook button renders disabled in DEV with an actionable error
("Facebook sign-in is not configured for this build…") and is hidden in
prod (safe-prod fallback). Same dev-loud / prod-hide pattern as the other
two providers.

The 503 failure copy diverges from Google/Apple by appending _"in a few
minutes"_ — Facebook has no JWKS rotation recovery (the trust anchor is
Graph itself, see § Facebook specifics), so an immediate retry won't help
and the copy sets that expectation explicitly. The 401 copy matches the
Google/Apple precedent verbatim with the provider name swapped.

**Email-permission decline (no FE-side branching in slice 3):** Facebook
lets the user decline the `email` scope at consent time. The BE handles
that case (creates a row with `email=None`, fallback display name
`name → "ThreadLoop user"`) and the FE just lets sign-in complete; `/me`
renders the display name without an email line. The "ask the user to
re-grant email permission" reconsent prompt is deferred to a future
account-recovery Epic, where it has a real destination — adding a
nag-prompt for a feature that doesn't exist yet would be friction without
purpose. See ux-designer advisory on #39 § 1 for the full reasoning.

`link_required` responses on the Facebook branch are intercepted by the
slice-4 `LinkAccountsDialog` (see § Link UI (slice 4 / #40)). In practice
the branch is unreachable for Facebook because Graph API doesn't expose
`email_verified` and the BE treats every Facebook email as unverified —
see § Facebook specifics for the full analysis. The handling is kept
verbatim so a future Graph response shape change plugs in cleanly.

### Link UI (slice 4 / #40)

`frontend-web/src/components/LinkAccountsDialog.tsx` replaces the slice-1/2/3
generic _"This email is registered with another provider"_ error with a
real account-linking flow. When any of the three callbacks
(`/api/auth/{google,apple,facebook}/callback`) returns
`linkRequired: true`, `SignInPage` no longer surfaces a page-level error —
it stashes the `{ linkToken, linkProvider }` in component state and mounts
the modal.

**Page-state shape.** The modal lives as a state overlay on `/sign-in`,
not a separate `/link` route. The `linkToken` lives in `SignInPage`
component state for the duration of the flow and is _never_ persisted to
localStorage / sessionStorage / cookies — a page reload provably wipes
it (matches the AC's in-memory-only constraint and means a refresh is the
"start over" recovery path even when the BE-side TTL hasn't expired).

**Failure-envelope mapping** (matches PR #64's "Failure mapping" verbatim):

- **401** — link token expired / consumed / mismatched original provider
  / credential failed verification. The BE deliberately collapses these
  into one envelope so a probe can't distinguish them; the FE matches
  with one recovery copy ("Your linking session expired. Please sign in
  again to start over.") and a single _Back to sign-in_ CTA.
- **409** — second-provider identity already claimed by a different
  ThreadLoop account. Distinct copy ("This account is already linked to
  a different ThreadLoop account.") with no retry — retrying won't help.
- **503** — original provider's JWKS / Graph API unreachable. Retryable
  ("Couldn't reach the sign-in service just now. Please try again in a
  moment.").

**Client-side TTL.** A 10-minute timer matching the BE's default
`link_token_ttl_seconds=600` proactively transitions the modal to the
expired-recovery state if the user lingers. The BE has already
invalidated the token at that point, so any subsequent click would 401
anyway; the timer is a UX courtesy that swaps "click and get a confusing
error" for "see the recovery copy directly."

**Highlighted-button treatment.** The original-provider button is wrapped
in a `ring-2 ring-brand ring-offset-2` div with an "Original account"
badge above. The provider's _own_ button styling is preserved unchanged
(Google's GIS-rendered button isn't recolored — Google brand guidelines
forbid that; Apple/Facebook buttons keep their existing brand styles).
The modal renders the highlighted button regardless of whether that
provider's `VITE_*_ENABLED` flag is on, because at this point we're
confirming an _existing_ identity, not offering the provider for
sign-in — the flag's "this provider is offered" semantic doesn't apply.

**Accessibility.** Implemented per the WAI-ARIA APG dialog pattern:
`role="dialog" + aria-modal="true"`, `aria-labelledby` referencing the
heading id, `aria-describedby` referencing the explainer copy, initial
focus on the highlighted original-provider button (primary action wins
focus per APG), focus trap (Tab/Shift+Tab cycle inside the modal only),
Esc closes + restores focus to the originally-clicked second-provider
button. Status announcements live in a separate `aria-live="polite"`
region inside the modal, distinct from the page-level
`aria-live="assertive"` error region — the polite region won't interrupt
JAWS / NVDA mid-sentence during the SDK round-trip. The focus-trap
implementation is a minimal local hook (`src/lib/focusTrap.ts`); we did
not pull in `@radix-ui/react-dialog` or `@headlessui/react` for one
consumer.

**Wrong-provider re-auth.** While the modal is open the underlying
second-provider buttons remain technically clickable (they sit behind
the modal's overlay). A click on a non-original-provider button is
treated as a user mistake (e.g. two browser tabs) — the modal stays up
and surfaces an inline message ("Sign in with {originalProvider} to
link your accounts.") rather than starting a fresh callback round-trip
that would clobber the link state.

**Test coverage.**

- `frontend-web/src/components/LinkAccountsDialog.test.tsx` (Vitest):
  the five a11y AC bullets, the 401/409/503 mapping, the 10-minute TTL
  transition, the no-`linkToken`-in-storage assertion, and the per-
  provider re-auth happy path (Apple-as-original + Facebook-as-original
  in unit tests; Google-as-original in Cypress because GIS round-trip
  asynchrony is awkward in jsdom).
- `frontend-web/cypress/e2e/sign-in-link.cy.ts` (Cypress): full network-
  seam stub of the Google→Apple→`POST /api/auth/link` flow, the 401
  recovery branch, the Esc-restores-focus a11y bullet, and the wrong-
  provider re-auth branch. The Apple stub fires here even though
  `VITE_APPLE_ENABLED` defaults to `false` — the spec is configured to
  set the flag for the test bench since modal logic doesn't depend on
  the provider being live in production. BE-side coverage of the
  Google↔Apple↔Facebook matrix lives on PR #64.

### Out of scope here

The account-unlinking flow and the "manage linked accounts" settings UI
are deferred to future tasks — there's no UI for them yet because
there's no consumer. Mobile linking shipped with slice 5 (#20) using
the same `POST /api/auth/link` contract — see § "Mobile client (slice
5 / #20)" below.

## Mobile client (slice 5 / #20)

The Expo / React Native client ships with the mobile equivalents of
the slice-1/3/4 web surfaces. Google + Facebook sign-in on iOS and
Android, the same `AuthContext` three-state machine, and the same
`LinkAccountsModal` for cross-provider account-linking. Apple is
descoped per § "Per-provider gating" and § "Deferred providers" in
RFC 0001 — `expo-apple-authentication` is **not** bundled in this
slice.

### Per-provider gating — `EXPO_PUBLIC_*` flags

The mobile client mirrors the web client's per-provider flag pattern.
Strict `=== "true"` parse: any value other than the literal string
`true` (including unset) resolves to `false`. The flags live in
`frontend-mobile/.env.example`:

- `EXPO_PUBLIC_GOOGLE_ENABLED` (default `true` in `.env.example`).
- `EXPO_PUBLIC_FACEBOOK_ENABLED` (default `true`).
- `EXPO_PUBLIC_APPLE_ENABLED` (default `false`; gate exists for
  symmetry with web but flipping it alone won't surface a button —
  `expo-apple-authentication` is not bundled).
- Per-platform Google OAuth client IDs:
  `EXPO_PUBLIC_GOOGLE_CLIENT_ID_IOS` and
  `EXPO_PUBLIC_GOOGLE_CLIENT_ID_ANDROID`. Distinct OAuth credentials
  from the same Google Cloud project as the BE's `GOOGLE_CLIENT_ID`
  (which is the web credential).
- `EXPO_PUBLIC_FACEBOOK_APP_ID` — same Meta App as the BE's
  `FACEBOOK_APP_ID`.

Setup walkthrough (Google Cloud Console flow, Meta App registration,
key hashes) lives in
[`frontend-mobile/README.md`](../frontend-mobile/README.md) §
"Sign-in setup".

### Auth context — `src/auth/AuthContext.tsx`

Same shape as the web client. Three-state machine
(`loading` / `anonymous` / `authenticated`), silent-refresh on first
mount via `POST /api/auth/refresh`. The mobile context adds two
deltas over web:

1. **Access JWT mirrored to `expo-secure-store`.** The token still
   lives in memory as the canonical reference for hot paths, but
   `setAccessToken` mirrors it into the platform secure store so a
   cold-start can hydrate the user view immediately. Refresh token
   continues to be the httpOnly cookie — React Native's `fetch`
   cookie jar respects it transparently with
   `credentials: "include"`.
2. **Network-failure fallback to stored token + `/api/me`.** If
   `POST /api/auth/refresh` fails with anything other than a 401
   (network error, 5xx, 429 — i.e. any transport or transient
   availability failure), the context tries the cached access token
   against `/api/me`. If that succeeds, the user lands on the
   authenticated state with the last-known profile and a stale-but-
   still-valid access token, and the `offline` flag on the context is
   set so screens can surface a "Working offline" banner. Only a
   genuine 401 from `/api/auth/refresh` clears the stored token and
   drops the user to anonymous — anything else preserves the cached
   session. If the fallback `/api/me` itself 401s, the stored token is
   cleared and the user lands on anonymous (so a server-side
   revocation eventually catches up). The cookie-only web client
   doesn't need this branch because its refresh path is the only
   auth-state reconstruction surface.

   **"Working offline" banner.** `AuthContext` exposes an
   `offline: boolean` alongside `state`. It flips to `true` whenever
   we hydrated via the degraded cached-token + `/api/me` path (any
   non-401 refresh failure with a cached session in
   `expo-secure-store`), and `MeScreen` renders a
   "Working offline — some features may be unavailable" banner while
   it's set. The flag clears on the next successful `signIn`,
   `signOut`, or refresh round-trip. Because the cached token is
   still re-validated against `/api/me` before the user is
   reauthenticated, a server-revoked session won't grant offline
   access — it'll 401 from `/api/me` and fall through to anonymous.

The secure-store wrapper at `src/auth/secureStore.ts` falls back to
an in-memory map on web / jsdom (where `expo-secure-store` throws)
so `expo start --web` and jest both work without conditional code in
the call sites.

### Per-provider sign-in — `expo-auth-session`

Google and Facebook share the same SDK (`expo-auth-session`) on
mobile. The provider modules wrap `useIdTokenAuthRequest` (Google)
and `useAuthRequest` (Facebook) with typed extractors so the
`SignInScreen` doesn't need to know the SDK shape:

- `src/auth/google.ts` — exposes `useGoogleAuth()` and
  `extractGoogleIdToken()`. The hook requests the OIDC `id_token`
  scope explicitly so the backend's `POST /api/auth/google/callback`
  receives the same shape the web client posts.
- `src/auth/facebook.ts` — exposes `useFacebookAuth()` and
  `extractFacebookAccessToken()`. The `email` permission is requested
  but optional; declining it lets sign-in complete with
  `email=null` (mirrors web).

The Google provider needs per-platform client IDs because the Google
Cloud Console issues distinct OAuth credentials per platform.
`expo-auth-session` picks the right one based on `Platform.OS` at
runtime. Apps Store-side bundle ID validation against
`com.threadloop.app` (from `app.json -> ios.bundleIdentifier`) is
done on the OAuth credential — the SDK doesn't need to know about
it.

### Sign-in flow

1. User opens the app cold. `AuthProvider` mounts and runs the
   silent-refresh round-trip.
2. If the refresh cookie is valid, the user lands on `MeScreen`
   directly.
3. Otherwise the user lands on `SignInScreen` with Google +
   Facebook buttons.
4. Tapping a button calls `promptAsync()` from the matching
   `expo-auth-session` provider, which opens the in-app browser
   (Safari View Controller on iOS, Custom Tabs on Android) on the
   provider's consent screen.
5. On consent, the provider redirects back to the app via the
   `threadloop://` deep-link scheme registered in `app.json`. The
   SDK resolves the in-flight `useAuthRequest` promise with the
   credential.
6. The screen extracts the credential (`id_token` for Google,
   `accessToken` for Facebook) and posts to the matching
   `/api/auth/{provider}/callback`. Same wire shape as web.
7. On `Session.linkRequired: true`, `LinkAccountsModal` opens with
   the link token + original provider. The user re-auths with the
   original provider, the modal posts to `POST /api/auth/link`, and
   on 200 the merged session is promoted via `useAuth().signIn()`.
8. On a happy `Session`, `useAuth().signIn()` switches
   `RootNavigator` to `MeScreen` showing display name, email, and
   provider.

### Sign-out

Tapping **Sign out** on `MeScreen` posts `POST /api/auth/logout`
(which revokes the refresh cookie BE-side), clears the
`expo-secure-store` mirror, and drops the in-memory state to
`anonymous`. Idempotent — if the logout call fails the FE still
returns to anonymous (refresh cookie is revoked next time the user
signs in).

### `LinkAccountsModal` — `src/components/LinkAccountsModal.tsx`

Mirrors `frontend-web/src/components/LinkAccountsDialog.tsx`. Same
state machine (`idle` / `exchanging` / `expired` / `conflict` /
`unreachable`), same failure mapping (401 / 409 / 503 per PR #64's
"Failure mapping"), same 10-minute client-side TTL matching the
BE's default `link_token_ttl_seconds=600`. Differences from web:

- Uses RN `<Modal>` (which natively handles the back-button on
  Android and the swipe-to-dismiss gesture on iOS) rather than a
  div overlay.
- `accessibilityLiveRegion="polite"` on the status text rather than
  the web's `aria-live="polite"` region. Same intent — JAWS / NVDA
  on web, VoiceOver / TalkBack on mobile.
- 44pt touch targets on the close button (`width: 44, height: 44`)
  for iOS HIG / Material accessibility compliance.
- Apple is not a possible original provider in the active mobile
  build (`expo-apple-authentication` not bundled). If the BE ever
  returns `linkProvider: "apple"` on a mobile callback — which
  shouldn't happen while `APPLE_ENABLED=false` server-side — the
  modal renders a "contact support" recovery message rather than a
  non-functional Apple button.

### Out of scope in this slice

- Apple-on-iOS native sign-in (Epic #57).
- Push notifications and deep links beyond `expo-auth-session`'s
  redirect URI handling.
- Detox E2E tests — set up per release, not per PR.
- EAS / store-signing config (separate `Infra` task tied to first
  publish).

## What's not implemented yet

Epic #11 is closed with slice 5. Still open under their own surfaces:

- Apple sign-in re-activation (Epic #57 — re-enters scope when the
  project owner enrolls in the Apple Developer Program and prepares
  for App Store submission; the existing web + BE code is gated
  off, not removed).
- `require_buyer` / `require_seller` dependencies (#37 — defer to
  listings / transactions epics where the first consumers land).
- Scheduled `client_secret` JWT rotation job (RFC 0001 § Risks —
  moot while Apple is deferred; revisit at re-activation).
- Account-unlinking flow and the "manage linked accounts" settings
  UI. Deferred until there's a consumer.

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
  responses on slice 1 surfaced as a generic error string; slice 4
  (#40) replaced that with the `LinkAccountsDialog` modal. Auth
  context conventions documented above under "Web client (slices 1,
  2, 3 & 4 — Google + Apple + Facebook + linking)".
- **Slice-2 FE** (#38) — _shipped to main 2026-05-04, deferred from
  product_: Apple sign-in button on `/sign-in` next to the Google one,
  wired via the Sign in with Apple JS SDK. Posts `{ idToken, code,
name? }` to `POST /api/auth/apple/callback`; on success follows the
  same redirect path as Google (`?next=` or `/`). `link_required`
  reuses the slice-1 generic-error path; the full link UI is still
  slice 4. Apple-relay email accounts flow through end-to-end
  (backend's `is_private_email` bypass plus an FE that doesn't
  special-case email shapes). New env vars: `VITE_APPLE_CLIENT_ID`
  (required when `APPLE_ENABLED=true`) and `VITE_APPLE_REDIRECT_URI`
  (optional). Cypress smoke at `cypress/e2e/sign-in-apple.cy.ts`.
  **Disabled by default in every deployment** (`APPLE_ENABLED=false`,
  `VITE_APPLE_CLIENT_ID` unset → button renders disabled and is hidden
  by the polish task #56) — re-activation requires Apple Developer
  Program enrollment per RFC 0001 § "Deferred providers". The Apple
  branches of the auth surface continue to be exercised by tests and
  by the dev stack (when an operator opts in by setting the flag and
  secrets locally).
- **Slice-3 FE** (#39): Facebook sign-in button on `/sign-in` next to
  the Google + Apple ones, wired via the Facebook Login SDK
  (`https://connect.facebook.net/en_US/sdk.js`). Posts `{ accessToken }`
  to `POST /api/auth/facebook/callback`; on success follows the same
  redirect path as Google + Apple (`?next=` or `/`). The `email`
  permission is requested but optional — when the user declines it, the
  BE persists `email=null` and the FE just lets sign-in complete (the
  reconsent prompt is deferred to a future account-recovery Epic). The
  503 failure copy diverges from Google/Apple by appending _"in a few
  minutes"_ — Facebook has no JWKS rotation recovery so an immediate
  retry won't help. New env vars: `VITE_FACEBOOK_ENABLED` and
  `VITE_FACEBOOK_APP_ID` (required when `FACEBOOK_ENABLED=true`).
  Brand colour `#1877F2` lives in `tailwind.config.js` as the
  `facebook` token. Cypress smoke at
  `cypress/e2e/sign-in-facebook.cy.ts`. **Disabled by default in every
  deployment** (`FACEBOOK_ENABLED=false`) until a registered Meta App
  ID is wired into both the BE and FE flag pair — the live demo flip
  is gated on Meta App registration.
- **Slice-4 BE half** (#18): `POST /api/auth/link` — consumes the
  `link_token` issued by a callback's `link_required` envelope, re-verifies
  the original-provider credential, and merges the second-provider identity
  onto the existing user's `user_identities` rows. Schema additions:
  `user_identities` (source of truth for "all identities a user can sign
  in with"; primary identity backfilled by migration 0003 from the legacy
  `users.(provider, provider_user_id)` columns) and `consumed_link_tokens`
  (single-use enforcement keyed on `link_token.jti`). Callbacks now look
  users up via `app.auth.identity.find_user_by_identity` rather than
  scanning `users.(provider, provider_user_id)` directly. `User.provider`
  on the wire continues to expose the primary provider as a singular
  scalar — no contract bump on `User`. The endpoint is wired and tested
  end-to-end across all three providers (Apple integration is exercised
  by the test matrix even though `APPLE_ENABLED=false` keeps it dormant
  in active deployments). Apple-relay bypass honored end-to-end: relay
  sign-ins never produce a link_token, so the link route is unreachable
  for relay addresses. Cleanup of expired `consumed_link_tokens` rows is
  a future concern; rows are tiny and accumulation is acceptable. See
  `docs/auth.md` § "Account linking" for the full resolved semantics.
- **Slice-4 FE half** (#40): `LinkAccountsDialog` modal that replaces the
  slice-1/2/3 generic _"registered with another provider"_ error with a
  real cross-provider account-linking flow. When a callback returns
  `linkRequired: true`, `SignInPage` mounts the modal with the original-
  provider button highlighted (`ring-2 ring-brand ring-offset-2` wrapper
  + "Original account" badge); the user re-authenticates with the
  original provider, the modal posts `{linkToken, originalProvider,
  credential}` to `POST /api/auth/link`, and on 200 the merged session
  is promoted via `useAuth().signIn()` and the user redirected to `next`.
  The modal lives as a state overlay on `/sign-in` (not a separate
  route) so a reload provably wipes the in-memory `linkToken`. Failure
  envelopes from PR #64 are mapped to distinct copy: 401 →
  expired-or-mismatched single-recovery copy, 409 → identity-already-
  claimed (no-retry), 503 → provider-unreachable (retryable). A 10-
  minute client-side TTL matching the BE's default
  `link_token_ttl_seconds=600` proactively transitions the modal to the
  expired-recovery state. Accessibility per WAI-ARIA APG dialog pattern:
  `role="dialog" + aria-modal="true"` with `aria-labelledby` /
  `aria-describedby`, initial focus on the highlighted original-provider
  button, focus trap (Tab/Shift+Tab), Esc closes + restores focus to
  the originally-clicked second-provider button. SDK-flow status
  announcements live in a separate in-modal `aria-live="polite"` region,
  distinct from the page-level assertive error region. Wrong-provider
  re-auth (user clicks the second-provider button while the modal is
  open) surfaces an inline message rather than starting a fresh
  callback round-trip. Cypress smoke at
  `cypress/e2e/sign-in-link.cy.ts`; Vitest unit coverage at
  `src/components/LinkAccountsDialog.test.tsx`. Apple is the second
  provider in the smoke test bench despite `VITE_APPLE_ENABLED=false`
  in default builds because the modal logic doesn't gate on the flag —
  see § Web client / Link UI for the carve-out reasoning.
- **camelCase wire shape** (#44): the contract drift between
  `shared/openapi.yaml` (snake) and `shared/src/types/` (camel) inherited
  from #12 was resolved by flipping the wire to camelCase via Pydantic
  `alias_generator=to_camel + populate_by_name=True +
serialize_by_alias=True` (ADR 0009). The per-endpoint adapter slice 1
  shipped in `frontend-web/src/api/client.ts` is retired; the web and
  mobile clients both consume the typed shapes from `@threadloop/shared`
  directly with no boundary translation.
- **Slice-5 mobile** (#20): Expo / React Native client for the SSO
  sign-in flow on iOS and Android. `SignInScreen` renders Google +
  Facebook buttons via `expo-auth-session` (no native Apple — Apple
  was dropped from #20 per the 2026-05-04 scope revision; tracked
  under Epic #57). `AuthContext` mirrors the web three-state machine
  with two mobile-specific deltas: access JWT mirrored to
  `expo-secure-store` for cold-start hydration, and a network-failure
  fallback to the cached token + `/api/me` so offline users see their
  last-known profile rather than a sign-in screen. `LinkAccountsModal`
  mirrors the web `LinkAccountsDialog` state machine using RN
  primitives (`<Modal>`, `accessibilityLiveRegion="polite"`); 401 /
  409 / 503 failure mapping and 10-minute client-side TTL identical
  to web. RootNavigator is `@react-navigation/native-stack`. New env
  vars: `EXPO_PUBLIC_API_BASE_URL`, `EXPO_PUBLIC_{GOOGLE,APPLE,FACEBOOK}_ENABLED`,
  `EXPO_PUBLIC_GOOGLE_CLIENT_ID_{IOS,ANDROID}`,
  `EXPO_PUBLIC_FACEBOOK_APP_ID`. Apple gate stays `false` by default
  and is non-functional in this build (no `expo-apple-authentication`
  bundled). Setup walkthrough in
  [`frontend-mobile/README.md`](../frontend-mobile/README.md) §
  "Sign-in setup". Jest unit coverage at
  `frontend-mobile/src/auth/AuthContext.test.tsx` covers the
  silent-refresh / 401 / link-required-defensive /
  network-failure-fallback / signIn / signOut state transitions.
  This is the Epic-closing slice.
