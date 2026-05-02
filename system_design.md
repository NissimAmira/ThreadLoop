# ThreadLoop — System Design

> Source of truth for architecture, API contracts, and the relational schema.
> The OpenAPI spec at [`shared/openapi.yaml`](./shared/openapi.yaml) is the
> machine-readable contract; this document is the human-readable companion.

---

## 1. Architecture overview

```
                                     ┌──────────────┐
                          CDN ──────▶│ Object Store │  (images, .glb LODs)
                            │        └──────────────┘
                            │
   [Web / Mobile] ─▶ [API GW / LB] ─▶ [FastAPI pods (HPA)] ─┬─▶ [Postgres primary]
                                                            ├─▶ [Postgres read replicas]
                                                            ├─▶ [Redis: cache + rate-limit]
                                                            └─▶ [Meilisearch index]
                                                                       ▲
                                       [Worker pool: image derivatives, AR optimization]
```

### Cloud-agnostic primitives

| Need | Abstraction | Reference impl |
| --- | --- | --- |
| Object store | S3-compatible | AWS S3 / GCS / R2 / MinIO (local) |
| CDN | HTTP cache w/ signed URLs | CloudFront / Cloud CDN / Bunny |
| Queue | Redis Streams (MVP) → SQS/PubSub | Redis (compose) |
| Container runtime | OCI | Compose (dev) → Kubernetes (prod) |
| Secrets | Provider KMS | `.env` (dev) → SOPS / cloud KMS |

### Scaling concerns

- **Images** — direct-to-store presigned uploads; worker generates `thumb (256) / card (640) / detail (1280) / zoom (2048)` in WebP/AVIF; client uses `srcset`.
- **Search/read traffic** — Postgres read replicas serve `GET /listings/:id`; Meilisearch handles `GET /search`. Redis cache-aside fronts both.
- **AR assets** — `.glb` files stored under `ar/` prefix, compressed with Draco + Meshopt, served as LOD ladder (`low.glb` mobile / `high.glb` desktop) via CDN with `Range` requests for progressive streaming.

---

## 2. Authentication — SSO only

No password storage. Users authenticate exclusively via Google, Apple, or Facebook.

```
[Client] ─▶ Provider SDK ─▶ ID token (JWT)
                                │
                                ▼
                  POST /api/auth/{provider}/callback
                                │
                                ├─ verify signature against provider JWKS
                                ├─ extract sub, email, email_verified, name
                                ├─ upsert user (provider, provider_user_id)
                                └─ return ThreadLoop session
                                       (access JWT 15m + refresh in httpOnly cookie)
```

**Account linking.** When a user signs in with provider B using a verified email already associated with provider A, we present a "link account" prompt rather than silently merging. Apple's "Hide My Email" relay address prevents passive matching, so this is deliberate.

**Authorization model — buyer/seller dual role.** Every user can sell and buy. Capability flags on the row govern individual actions:
- `can_sell` — gated on completing seller onboarding (payout method).
- `can_purchase` — gated on a verified email or phone.

The `transactions` table references `buyer_id` and `seller_id` from the same `users` table, with a CHECK constraint preventing self-purchase.

---

## 3. Domain model

```
┌─────────┐         ┌──────────┐         ┌─────────────┐
│  User   │ 1 ───< │ Listing  │ 1 ───< │ ListingImage │
└─────────┘         └──────────┘         └─────────────┘
     │                  │
     │                  └──< ListingArAsset (0..1)
     │
     └──< Transaction >── User
              (buyer)         (seller)
```

### `users`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid pk | |
| `provider` | text not null | `google` / `apple` / `facebook` |
| `provider_user_id` | text not null | Provider's `sub` claim |
| `email` | text | May be null (Apple relay) |
| `email_verified` | boolean default false | |
| `display_name` | text not null | |
| `avatar_url` | text | |
| `can_sell` | boolean default false | |
| `can_purchase` | boolean default true | |
| `seller_rating` | numeric(3,2) | Cached aggregate, 0..5 |
| `created_at` | timestamptz default now() | |
| `updated_at` | timestamptz default now() | |
| | UNIQUE (`provider`, `provider_user_id`) | |

### `listings`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid pk | |
| `seller_id` | uuid fk → users(id) | |
| `title` | text not null | |
| `description` | text | |
| `brand` | text | |
| `category` | text not null | top-level taxonomy (`tops`, `bottoms`, ...) |
| `size` | text | |
| `condition` | text not null | `new` / `like_new` / `good` / `fair` |
| `price_cents` | integer not null check (price_cents > 0) | |
| `currency` | char(3) default 'USD' | ISO 4217 |
| `status` | text not null default 'draft' | `draft` / `active` / `sold` / `removed` |
| `search_tsv` | tsvector | maintained via trigger; mirrored to Meili |
| `created_at` | timestamptz default now() | |
| `updated_at` | timestamptz default now() | |

Indexes: `(status, created_at desc)`, GIN on `search_tsv`, `(brand)`, `(category, size)`.

### `listing_images`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid pk | |
| `listing_id` | uuid fk → listings(id) on delete cascade | |
| `position` | smallint not null | 0 = primary |
| `storage_key` | text not null | Object-store key for the master |
| `width`, `height` | integer | |
| `created_at` | timestamptz default now() | |

### `listing_ar_assets`

| Column | Type | Notes |
| --- | --- | --- |
| `listing_id` | uuid pk fk → listings(id) on delete cascade | one per listing |
| `glb_low_key` | text not null | Mobile LOD |
| `glb_high_key` | text not null | Desktop LOD |
| `processed_at` | timestamptz | null until worker finishes |

### `transactions`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid pk | |
| `listing_id` | uuid fk → listings(id) | |
| `buyer_id` | uuid fk → users(id) | |
| `seller_id` | uuid fk → users(id) | denormalized for analytics |
| `amount_cents` | integer not null | |
| `currency` | char(3) not null | |
| `status` | text not null | `pending` / `paid` / `shipped` / `delivered` / `disputed` / `refunded` |
| `created_at` | timestamptz default now() | |
| `updated_at` | timestamptz default now() | |
| | CHECK (`buyer_id <> seller_id`) | |

### `refresh_tokens`

Server-side record of issued refresh tokens. Plaintext tokens are never
stored — only their hash. Rotation: the row in use is marked
`revoked_at = now()` and a fresh row is inserted on each `/api/auth/refresh`.
Reuse of a revoked token signals likely theft and triggers revocation of
every row for that `user_id`. See [RFC 0001 § Schema additions](./docs/rfcs/0001-auth-sso.md).

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid pk | |
| `user_id` | uuid fk → users(id) on delete cascade | indexed |
| `token_hash` | bytea not null unique | hash of the opaque random token; never stored plaintext |
| `issued_at` | timestamptz not null default now() | |
| `expires_at` | timestamptz not null | 30 days from `issued_at` per RFC 0001 |
| `revoked_at` | timestamptz | null = active; non-null = revoked (logout, rotation, theft response) |

Indexes: `ix_refresh_tokens_user_id` for "revoke all tokens for user X".

---

## 4. API contracts

Versioned under `/api`. OpenAPI spec at [`shared/openapi.yaml`](./shared/openapi.yaml). Highlights:

### Health
- `GET /api/health` → `{ status, version, db, redis, meili }`. Status `ok` only if all dependencies respond.

### Auth (SSO)

The contract is `shared/openapi.yaml`. Summary:

- `POST /api/auth/{provider}/callback` — single endpoint dispatched by the
  `provider` path parameter (`google` | `apple` | `facebook`). Per-provider
  request body:
  - `google`:   `{ id_token }`
  - `apple`:    `{ id_token, code }`
  - `facebook`: `{ access_token }`
  Returns a `Session`. On verified-email collision with an existing account
  from a different provider, returns `200` with `link_required: true` and a
  short-lived `link_token`; `access_token`/`user` are absent in that state
  and the client must surface the linking prompt.
- `POST /api/auth/refresh` — reads the `refresh_token` httpOnly cookie,
  rotates it server-side, returns a new access JWT. Reuse of a rotated
  token revokes all of that user's refresh tokens (likely theft) and
  returns `401`.
- `POST /api/auth/logout` — revokes the current refresh token and unsets
  the cookie. Idempotent (`204` even with no cookie).
- `GET  /api/me` — returns the authenticated `User`.

Errors use a uniform `{ code, message, request_id? }` envelope (`Error`
schema). Error statuses returned by these endpoints:

- `400` malformed callback body (missing required field for provider).
- `401` invalid/expired provider token, missing/expired/revoked/reused
  refresh token, or missing access token on `/api/me`.
- `404` unknown provider in path; or auth subsystem disabled
  (`AUTH_ENABLED=false`) — applies uniformly to every `/api/auth/*` route
  AND `/api/me`, so a probe under flag-off can't tell the auth subsystem
  exists. See `docs/auth.md` § Feature flag.
- `503` provider JWKS unreachable; client retries.

### Listings
- `GET    /api/listings` — list active listings (cursor pagination)
- `GET    /api/listings/:id`
- `POST   /api/listings` — auth + `can_sell`
- `PATCH  /api/listings/:id` — owner only
- `DELETE /api/listings/:id` — owner only (sets `status=removed`)
- `POST   /api/listings/:id/images` — returns presigned upload URL
- `POST   /api/listings/:id/ar` — returns presigned upload URL for `.glb`

### Search
- `GET /api/search?q=&brand=&category=&size=&min_price=&max_price=&condition=&page=` — Meilisearch-backed; returns hits + facets.

### Transactions
- `POST /api/transactions` `{ listing_id }` — creates pending purchase
- `GET  /api/transactions/:id`
- `POST /api/transactions/:id/pay` — wires to payment provider (out of scope MVP, stubbed)
- `POST /api/transactions/:id/ship` — seller marks shipped
- `POST /api/transactions/:id/deliver` — buyer confirms

---

## 5. Search abstraction

```python
class SearchService(Protocol):
    async def index_listing(self, listing: Listing) -> None: ...
    async def remove_listing(self, listing_id: UUID) -> None: ...
    async def query(self, q: str, filters: Filters, page: Page) -> SearchResult: ...
```

Implementations: `MeiliSearch` (default), `PostgresSearch` (fallback for environments without Meili). Cutover path: dual-write during transition, then flip env var.

---

## 6. Observability

- Structured JSON logs with `request_id` propagated via `X-Request-Id`.
- `/api/health` for liveness + readiness.
- Prometheus metrics endpoint `/metrics` (FastAPI middleware) — request rate, latency histogram, DB pool stats.
- Sentry for client + server errors (env-gated).

---

## 7. Test phase strategy

| Layer | Tooling | Trigger |
| --- | --- | --- |
| Backend unit | Pytest + `pytest-asyncio` | Every PR |
| Backend integration | Pytest + Testcontainers (real Postgres) | Every PR |
| Contract | Schemathesis vs OpenAPI | Every PR |
| Web E2E | Cypress vs compose stack | Every PR (smoke) / nightly (full) |
| Mobile | Jest + Detox | Per release |
| Load | k6 vs staging | Pre-release |
| Security | Bandit, `npm audit`, Trivy | Every PR |
