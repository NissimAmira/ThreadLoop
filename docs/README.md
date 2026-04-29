# ThreadLoop documentation

| Document | What it covers |
| --- | --- |
| [`architecture.md`](./architecture.md) | High-level system architecture, infra primitives, scaling strategy for images / DB / AR assets, observability. |
| [`repository-structure.md`](./repository-structure.md) | Every folder in the monorepo explained, plus how the workspaces depend on each other. |
| [`development-cycle.md`](./development-cycle.md) | Dev → Test → Prod lifecycle. Branching, commit conventions, PR flow, CI gates, release cutting, deployment. |
| [`contributing.md`](./contributing.md) | How to add a feature end-to-end. Coding standards, testing expectations, common gotchas. |
| [`auth.md`](./auth.md) | SSO design, account linking across providers, dual-role buyer/seller authorization model. |
| [`search.md`](./search.md) | Meilisearch integration, the `SearchService` interface, swap path to/from Postgres FTS. |
| [`assets.md`](./assets.md) | Image upload pipeline, AR `.glb` processing, CDN strategy. |

The two contract documents live at the **repo root** because they're shared with non-developers and tooling:

- [`../system_design.md`](../system_design.md) — API contracts + SQL schema (human-readable).
- [`../shared/openapi.yaml`](../shared/openapi.yaml) — OpenAPI 3.1 spec (machine-readable).

When those two disagree, the OpenAPI file wins.
