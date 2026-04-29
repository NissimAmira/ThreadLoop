# CLAUDE.md

Quick-reference for Claude (and humans) working on ThreadLoop. Keep this file
**short and load-bearing** — push detail into [`docs/`](./docs) instead.

## What this repo is

A peer-to-peer second-hand fashion marketplace with AR try-on, structured as a
monorepo. Every user is both buyer and seller. SSO-only authentication
(Google / Apple / Facebook), no passwords.

## Workspaces

| Path | Stack | Purpose |
| --- | --- | --- |
| `backend/` | FastAPI + SQLAlchemy + Alembic + Pytest | HTTP API, schema migrations |
| `frontend-web/` | Vite + React + TS + Tailwind + Cypress | Web client |
| `frontend-mobile/` | Expo + React Native + TS | iOS/Android client |
| `shared/` | TypeScript types + `openapi.yaml` | Single source of truth for the API contract |
| `infra/docker/` | Docker Compose | Local dev stack |
| `.github/workflows/` | GitHub Actions | CI for each workspace + README/release automation |

## Authoritative documents

- [`system_design.md`](./system_design.md) — API contracts + SQL schema (the contract).
- [`shared/openapi.yaml`](./shared/openapi.yaml) — machine-readable contract.
- [`docs/architecture.md`](./docs/architecture.md) — infra, scaling, AR/asset pipeline.
- [`docs/development-cycle.md`](./docs/development-cycle.md) — Dev → Test → Prod workflow.
- [`docs/repository-structure.md`](./docs/repository-structure.md) — every folder explained.
- [`docs/contributing.md`](./docs/contributing.md) — branch/commit/PR conventions.

## Running things

```bash
make env          # one-time: copy .env.example to .env
make dev          # start postgres, redis, meilisearch, backend, web
make migrate      # apply Alembic migrations (in another terminal)
make test         # backend Pytest + web Vitest
make health       # curl the /api/health endpoint
make help         # list all targets
```

## Conventions (load-bearing)

- **Trunk-based branching.** Branch from `main` as `feat/<topic>` or `fix/<topic>`. Open a PR — never commit directly to `main`.
- **Conventional commits.** `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`. `release-please` reads these to cut releases.
- **Contract-first.** Any API change updates `shared/openapi.yaml` and the matching TS types in `shared/src/types/` in the same PR as the backend change.
- **Migrations are reversible.** Every Alembic revision must implement `downgrade()`.
- **Auth is SSO-only.** No password fields anywhere. Identity is `(provider, provider_user_id)`.
- **Search goes through `SearchService`.** Never query Meilisearch directly from a route — keep it swappable.
- **Buyer/seller dual role.** One `users` table with `can_sell` / `can_purchase` flags; `transactions` reference both `buyer_id` and `seller_id` from that table.

## When adding code

- **New backend route?** Add to `backend/app/routers/`, register in `app/main.py`, update `shared/openapi.yaml` + `shared/src/types/`, add Pytest coverage in `backend/tests/`.
- **New DB column or table?** Update SQLAlchemy model, run `make migration m="describe change"`, hand-review the generated revision, ensure `downgrade()` is correct.
- **New web component?** Co-locate the test (`Component.test.tsx`) next to the component. Use Tailwind utility classes; theme tokens live in `tailwind.config.js`.
- **Need a new external dependency?** Pin a version range and document why in the PR description.

## What NOT to do

- Don't bypass the `SearchService` interface, even temporarily.
- Don't add password / email-magic-link auth — SSO-only by design.
- Don't put environment-specific config in code; route it through `app/config.py` (backend) or `import.meta.env` (web) / `process.env.EXPO_PUBLIC_*` (mobile).
- Don't commit secrets. `.env` is gitignored; use `.env.example` for the template.
- Don't skip `downgrade()` in Alembic migrations.

## Where the AI should look first

For any task, read in this order:
1. The relevant doc in [`docs/`](./docs).
2. `system_design.md` if the task touches API / data model.
3. The actual code in the workspace you're modifying.
4. Existing tests for the surface you're changing — they encode the expected contract.
