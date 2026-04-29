# Search

ThreadLoop uses Meilisearch for marketplace search. All search calls go
through a `SearchService` interface so the backend can be swapped (to
Postgres FTS, Typesense, OpenSearch) without touching route code.

## Why Meilisearch (not Postgres FTS)

| Capability | Postgres FTS (`tsvector` + `pg_trgm`) | Meilisearch |
| --- | --- | --- |
| Exact + prefix match | ✅ | ✅ |
| Typo tolerance ("nikee" → "nike") | Approximate via trigram | ✅ first-class |
| Instant search (<50ms) | OK at small scale | ✅ designed for it |
| Faceted filters | Works, but you write the SQL | ✅ built-in, returned with results |
| Custom ranking | Manual `ts_rank` tuning | Declarative ranking rules |
| Synonyms | Manual dictionary | ✅ config |
| Stemming | Per-column language config | Automatic |
| Operational cost | Already running Postgres | Extra service |

For a marketplace where search is the headline UX, the gap is too big to
start without it.

## Interface

```python
# backend/app/services/search.py
class SearchService(Protocol):
    async def index_listing(self, listing: Listing) -> None: ...
    async def remove_listing(self, listing_id: UUID) -> None: ...
    async def query(
        self,
        q: str,
        filters: Filters,
        page: Page,
    ) -> SearchResult: ...
```

Routers depend on the protocol, never on a concrete client:

```python
@router.get("/search")
async def search(q: str = "", svc: SearchService = Depends(get_search)):
    return await svc.query(q, filters=...)
```

## Implementations

### `MeiliSearch` (default)

Talks to the Meilisearch HTTP API on port `7700`. The `listings` index is
configured with:

- **Searchable attributes**: `title`, `description`, `brand`, `category`.
- **Filterable attributes**: `brand`, `category`, `size`, `condition`,
  `price_cents`, `status`.
- **Sortable attributes**: `price_cents`, `created_at`.
- **Ranking rules**: `words → typo → proximity → attribute → exactness →
  created_at:desc` (newest first, all else equal).
- **Synonyms**: `sneakers ↔ trainers`, `tee ↔ t-shirt`, etc. (loaded from
  `infra/meili/synonyms.json` — added in the search PR).

The dashboard at `http://localhost:7700` is useful for exploring during
development; production deployments require `MEILI_MASTER_KEY` to access it.

### `PostgresSearch` (fallback)

Uses a `search_tsv` `tsvector` column on `listings`, maintained via trigger.
Faceting via `GROUP BY` queries against the same table. Slower and lacks typo
tolerance, but requires no extra service. Useful for:
- Tiny deployments / demos
- Testing the abstraction
- Disaster fallback if Meilisearch is unavailable

## Indexing

Indexing is **eventual**, not synchronous with writes:

1. A listing transition to `status='active'` (via `POST /listings/{id}` or a
   patch) emits an event to a Redis stream.
2. A worker consumes the stream and calls `SearchService.index_listing()`.
3. On failure, the worker retries with exponential backoff; persistent
   failures alert.

This decouples write latency from search-backend availability, which matters
because Meilisearch ingestion can be momentarily slow during reindexing.

## Reindex command

```bash
make reindex
# or directly:
python -m app.cli reindex --batch-size 500
```

Iterates active listings, sends each to `SearchService.index_listing()`. Used
on:
- First-time setup of a new search backend.
- Recovery after Meilisearch data loss.
- Schema changes that need a rebuilt index.

## Cutover (e.g. switching backends)

1. Stand up the new backend in parallel.
2. Enable **dual-write**: both implementations called from the worker.
3. Run `make reindex` against the new backend.
4. Flip `SEARCH_BACKEND` env var on the API; redeploy.
5. Monitor for a soak period.
6. Disable dual-write; remove the old backend.

The interface guarantees that route code never changes during this dance.

## What's not implemented yet

The scaffold has Meilisearch in the local stack and the contract for
`/api/search`. The `SearchService` interface, the indexing worker, and the
reindex command land in `feat/search`.
