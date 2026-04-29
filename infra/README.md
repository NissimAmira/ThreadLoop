# `infra/`

Infrastructure for ThreadLoop. Today this folder contains:

- `docker/docker-compose.yml` — local development stack (postgres, redis,
  meilisearch, backend, web).

## What's NOT here yet

The repository follows a **phased DevOps roadmap**. Cloud deployment
configs, monitoring stack manifests, Kubernetes manifests, and Helm charts
land in this folder when their phase trigger fires — not before.

> See [`../docs/devops-roadmap.md`](../docs/devops-roadmap.md) for the
> full plan, the explicit triggers, and the rationale for the phasing.

## Production images

Production-ready Dockerfiles live with each service:

| Service | Path | Notes |
| --- | --- | --- |
| Backend | [`../backend/Dockerfile.prod`](../backend/Dockerfile.prod) | Multi-stage, no dev deps, multiple uvicorn workers, runs as non-root. |
| Web frontend | [`../frontend-web/Dockerfile.prod`](../frontend-web/Dockerfile.prod) | Multi-stage; builds the Vite bundle, serves via nginx with SPA fallback and hashed-asset caching. |

CI validates both build successfully on every PR (no push). Image push to
a registry wires up in **Phase 1 — first real deployment** of the
roadmap.

### Building locally

```bash
# Backend
docker build -f backend/Dockerfile.prod -t threadloop-backend:dev backend

# Web frontend (context = repo root because it copies from shared/ + frontend-web/)
docker build -f frontend-web/Dockerfile.prod -t threadloop-web:dev .

# Run them
docker run --rm -p 8000:8000 threadloop-backend:dev
docker run --rm -p 8080:80 threadloop-web:dev
```

### Building with custom config

The web frontend bakes `VITE_API_URL` at build time. To target a real
backend:

```bash
docker build \
  -f frontend-web/Dockerfile.prod \
  --build-arg VITE_API_URL=https://api.threadloop.example \
  -t threadloop-web:prod .
```

The backend reads its config from environment variables at runtime — no
build-time config needed.

## Deploying

> **Not yet.** When Phase 1 of the roadmap fires, this README will be
> updated with the actual deployment commands and target. Until then, the
> images build but are not pushed anywhere.
