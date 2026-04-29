# Architecture

## System diagram

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

## Cloud-agnostic primitives

ThreadLoop is designed against abstract primitives, not a specific cloud:

| Need | Abstraction | Reference impls |
| --- | --- | --- |
| Object store | S3-compatible | AWS S3 / GCS / R2 / Backblaze / MinIO (local) |
| CDN | HTTP cache + signed URLs | CloudFront / Cloud CDN / Bunny / Fastly |
| Queue | Redis Streams (MVP) → managed | SQS / Pub/Sub / RabbitMQ |
| Container runtime | OCI | Docker Compose (dev) → Kubernetes / Fly / ECS (prod) |
| Secrets | KMS + secret manager | SOPS / AWS SM / GCP SM / Vault |

The local stack uses Postgres, Redis, and Meilisearch in containers
(`infra/docker/docker-compose.yml`) so dev parity is high.

## Scaling strategies

### Images (high-res clothing photos)

1. **Direct-to-store presigned uploads.** API issues a short-lived PUT URL; the
   client uploads directly to object storage. The API never proxies bytes.
2. **Asynchronous derivative ladder.** A worker generates four sizes in WebP/AVIF:
   `thumb (256) / card (640) / detail (1280) / zoom (2048)`.
3. **Immutable, content-hashed URLs** behind a CDN with long TTLs. Clients use
   `srcset` so mobile gets `card`, desktop gets `detail`.
4. **EXIF stripped, virus-scanned, dimensions validated** before the listing
   transitions from `draft` to `active`.

### Database (high read traffic on search/listing detail)

1. **Read replicas** behind a router for `GET /listings/:id` and search hydration.
2. **PgBouncer** (transaction mode) for connection pooling.
3. **Redis cache-aside** for hot listings, category facets, seller profiles
   (60s TTL, invalidated on write).
4. **Partitioning** of `transactions` by month once volume justifies it.

### AR / 3D assets (`.glb` files)

1. **Object store** with an `ar/` prefix and stricter cache headers
   (`immutable`, 1y TTL).
2. **Draco + Meshopt compression** in the worker pipeline (typically 60–80%
   smaller).
3. **LOD ladder** (`low.glb` mobile / `high.glb` desktop) selected client-side
   from device capability hints.
4. **Range-request streaming** so the viewer can render geometry progressively.
5. **Signed URLs** for user-uploaded models to prevent hotlinking.

## Search

A `SearchService` Protocol (interface) separates routing logic from the search
backend.

```python
class SearchService(Protocol):
    async def index_listing(self, listing: Listing) -> None: ...
    async def remove_listing(self, listing_id: UUID) -> None: ...
    async def query(self, q: str, filters: Filters, page: Page) -> SearchResult: ...
```

Default implementation: **Meilisearch** (typo-tolerant, faceted, instant).
Fallback implementation: **PostgresSearch** (`pg_trgm` + `tsvector`) for envs
without Meili. Cutover is an env-var flip; dual-write during transition is
supported.

See [`search.md`](./search.md) for details and the swap procedure.

## Authentication

SSO-only. Users authenticate exclusively via Google, Apple, or Facebook ID
tokens, which the API exchanges for a ThreadLoop session. Account linking is
explicit (we don't silently merge accounts on email match because Apple's
"Hide My Email" relay address makes that unsafe).

See [`auth.md`](./auth.md) for the full flow and schema.

## Observability

- **Structured JSON logs** with `request_id` propagated via `X-Request-Id`.
- **`/api/health`** for liveness + readiness (db / redis / meili).
- **Prometheus metrics** at `/metrics` (FastAPI middleware): request rate,
  latency histogram, DB pool stats.
- **Sentry** for client + server errors (env-gated).

## Environments

| Env | Hosting | Data | Purpose |
| --- | --- | --- | --- |
| **Dev** | Local Compose / PR previews | Seeded fixtures | Fast iteration |
| **Test** | Staging cluster (mirrors prod) | Anonymized prod snapshot | Integration, E2E, load |
| **Prod** | Multi-AZ cluster, blue/green | Live data | Live marketplace |

`main` auto-deploys to staging. Tagged releases (`v*`) promote to production.
