# Authentication

ThreadLoop is **SSO-only**. There are no passwords stored anywhere.

## Why SSO-only

- **No credential storage** = no credential breach surface.
- **Better UX** — users sign in with the account they already have.
- **App Store compliance** — Apple requires Sign in with Apple if any other
  social login is offered (Guideline 4.8).
- **Fewer flows to maintain** — no signup form, password reset, email
  verification, MFA enrollment, etc.

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
```

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
`feat/auth-sso`:
- Provider SDK integration (web + mobile)
- `/api/auth/*` routes
- Session JWT + refresh-cookie middleware
- Account-linking flow
- `require_buyer` / `require_seller` dependencies

Until that PR ships, `users` rows can be inserted via Alembic seed data for
local development.
