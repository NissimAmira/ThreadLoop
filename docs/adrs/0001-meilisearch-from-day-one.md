# ADR 0001: Adopt Meilisearch from day one

- **Status:** Accepted
- **Date:** 2026-04-29
- **Context links:** `docs/search.md`

## Context

A marketplace's search UX is its headline feature — typo tolerance,
faceted filtering, and instant results are what users notice. Two
viable starting points: Postgres FTS (`tsvector` + `pg_trgm`), or a
dedicated engine like Meilisearch.

Postgres FTS is "free" — no extra service to run — but lacks first-class
typo tolerance, has manual ranking tuning, and faceting requires
hand-written `GROUP BY` queries. Meilisearch is a separate service but
solves all three out of the box.

## Decision

Use **Meilisearch from day one**, behind a `SearchService` interface so
the backend can be swapped later if needed. The local dev stack
(`docker-compose.yml`) includes Meilisearch from the scaffold PR.

## Consequences

- (+) Search UX is "Depop/Vinted-grade" from the first feature
  shipped, not retrofitted later.
- (+) The `SearchService` interface keeps the choice swappable — if we
  ever want Postgres FTS or Typesense, only the implementation changes.
- (−) One extra service in the dev stack and the deploy footprint.
- (−) Operational learning curve for Meilisearch (settings, indexing,
  synonyms) instead of leaning on existing Postgres expertise.

## Alternatives considered

**Postgres FTS first, swap later.** Cheaper to start. Rejected because
"swap later" is rarely cheap in practice, and the typo-tolerance gap
would degrade demo perception of an early-stage marketplace.

**Typesense / OpenSearch / Algolia.** Equivalent feature set. Algolia is
hosted (no infra) but expensive at scale; OpenSearch is heavier
operationally; Typesense is roughly comparable to Meilisearch.
Meilisearch's developer ergonomics edge it out for a portfolio repo.
