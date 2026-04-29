# ThreadLoop

A peer-to-peer second-hand fashion marketplace with AR try-on, built as a portfolio-grade fullstack monorepo.

<!-- STATUS:START -->
| Pipeline | Status |
| --- | --- |
| Backend CI | ![Backend CI](https://github.com/NissimAmira/ThreadLoop/actions/workflows/backend-ci.yml/badge.svg) |
| Web CI | ![Web CI](https://github.com/NissimAmira/ThreadLoop/actions/workflows/frontend-web-ci.yml/badge.svg) |
| Mobile CI | ![Mobile CI](https://github.com/NissimAmira/ThreadLoop/actions/workflows/frontend-mobile-ci.yml/badge.svg) |
| Release | ![Release](https://github.com/NissimAmira/ThreadLoop/actions/workflows/release-please.yml/badge.svg) |
<!-- STATUS:END -->

## What it is

Every user is both a buyer and a seller. List clothing with high-res photos and an optional `.glb` 3D asset, and other users can browse, search, and try the item on in AR before they buy.

- **Backend** — FastAPI + SQLAlchemy + Alembic, Postgres, Redis, Meilisearch.
- **Web** — Vite + React + TypeScript + Tailwind.
- **Mobile** — Expo + React Native + TypeScript with AR viewer.
- **Auth** — SSO only (Google / Apple / Facebook). No password management.
- **Search** — Meilisearch for instant, typo-tolerant marketplace search.
- **Assets** — object-store + CDN with image derivatives and Draco-compressed glTF.

See [`system_design.md`](./system_design.md) for the full architecture, API contracts, and SQL schema.

## Quick start

```bash
git clone https://github.com/NissimAmira/ThreadLoop.git
cd ThreadLoop
make env          # creates .env from .env.example
make dev          # starts postgres, redis, meilisearch, backend, web
make migrate      # applies database migrations (in another terminal)
```

Then open:
- Web app: http://localhost:5173
- API docs: http://localhost:8000/docs
- Health check: http://localhost:8000/api/health

The web app footer shows a status pill that polls the API every 30s — green = healthy, red = degraded.

Run `make help` to see all available tasks.

## Repository layout

```
threadloop/
├── backend/          FastAPI + SQLAlchemy + Alembic + Pytest
├── frontend-web/     Vite + React + TS + Tailwind + Cypress
├── frontend-mobile/  Expo + React Native + TS
├── shared/           TS types + openapi.yaml (single source of truth)
├── infra/
│   ├── docker/       compose.yml + per-service Dockerfiles
│   └── github/       (workflows live in .github/workflows)
├── system_design.md  API contracts + SQL schema + ERD
├── Makefile          `make dev`, `make test`, `make migrate`, ...
└── .github/          CI workflows + PR template
```

## Development workflow

1. Branch from `main`: `git checkout -b feat/<topic>`.
2. Open a PR — CI runs lint, type-check, and tests for changed packages.
3. Merge to `main` triggers staging deploy and updates the auto-generated sections of this README.
4. Tagged releases (`v*`) promote to production.

<!-- ROADMAP:START -->
## Roadmap

- [x] Monorepo scaffold + health-check flow
- [ ] SSO authentication (Google / Apple / Facebook)
- [ ] User profiles & seller onboarding
- [ ] Listings CRUD + image pipeline
- [ ] Meilisearch integration + faceted search
- [ ] Transactions & escrow
- [ ] AR viewer (web + mobile)
- [ ] Reviews & seller ratings
- [ ] Production infrastructure (Terraform)
<!-- ROADMAP:END -->

## License

[MIT](./LICENSE)
