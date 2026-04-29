# DevOps roadmap — phased plan with explicit triggers

This document captures the deployment, monitoring, and orchestration plan
for ThreadLoop. The principle:

> **Add a capability when the cost of *not* having it crosses a specific
> threshold, not when the capability itself feels mature enough to ship.**

Each phase below has a concrete trigger. Do not introduce the next phase
until its trigger has fired.

---

## For future Claude (or human) sessions

This is a **load-bearing document**. When you observe that a trigger
condition has fired (e.g., "the user is about to merge their first
non-trivial DB migration to a single-environment prod"), **proactively
prompt the user**:

> *"This change matches trigger X for phase Y in `docs/devops-roadmap.md`.
> Want me to scaffold the corresponding capability now or stay focused on
> the current task?"*

Don't wait for the user to remember the roadmap — that's what this
document is for. The trigger scan should happen at the start of any
session that touches deployment, infrastructure, performance, or
observability.

Triggers are listed in priority order. If trigger 3 has fired but 1 and 2
haven't, raise 1 and 2 first; phases ship in order.

---

## Current phase

**Phase 0 — local-only, scaffold complete.** Nothing is deployed. The
repository contains:

- Production Dockerfiles for `backend/` and `frontend-web/` (built in CI on
  every PR but not pushed anywhere yet).
- Local Docker Compose stack for development.
- CI gating, branch protection, release-please, the CR subagent.

There are zero user-facing features end-to-end. The next product
workstream is `feat/auth-sso`.

---

## Phases

### Phase 1 — first real deployment

**Trigger:** Auth + at least one user-facing feature (listings CRUD or
search) work end-to-end locally. The repo has something worth deploying.

**Adds:**

- **Container registry** — GitHub Container Registry (GHCR), free,
  integrated. New CI step: push images on `v*` tag.
- **Cloud target — Fly.io** for the backend + managed Postgres;
  **Cloudflare Pages** or **Vercel** for the frontend. Reasons: free tier
  covers a portfolio app, Anycast routing is real production infra,
  `fly.toml` reads almost like a k8s manifest, multi-region scale-out
  built-in. Migration path to k8s in Phase 4 is cheap because the config
  shapes line up.
- **Sentry** integration for error tracking. Env-gated; no-op without DSN.
- **`/metrics`** Prometheus endpoint enabled in production (FastAPI
  middleware — see `docs/architecture.md` § Observability).
- A **single environment called "prod"**. Be honest in the README — it's
  not "prod with staging discipline" yet, it's "first live URL." Staging
  arrives in Phase 2.
- Update `infra/README.md` with actual deployment commands, replacing the
  current "this isn't here yet" note.

**Why not k8s here:** A single backend service does not need
orchestration. Fly.io's machine model handles rolling deploys, health
checks, multi-region scale-out — without writing manifests.

**Approximate effort:** 1–2 days.

### Phase 2 — staging environment

**Trigger:** ANY of the following, whichever happens first:

- First non-trivial DB migration is queued for prod (anything beyond
  adding a nullable column).
- An external integration (Stripe webhooks, Apple/Google OAuth callbacks,
  S3 presigned uploads) needs a non-`localhost` URL to validate against.
- You catch yourself thinking *"I want to test this somewhere safe before
  prod"* on a routine PR.

**Adds:**

- Second Fly.io app (or k8s namespace, depending on Phase 4 status) for
  staging.
- Auto-deploy from `main` to staging on every merge.
- Tag promotion: `v*` tags promote staging-tested artifacts to prod.
- Anonymized snapshot of prod data, refreshed nightly (script in `infra/`).
- Database backup + restore drill — at least one documented dry run.

**Why not earlier:** without users on prod, "staging" is just "another
deploy." It earns its keep when shipping to prod is risky enough that you
want to test in a prod-shaped environment first.

**Approximate effort:** 1 day after Phase 1.

### Phase 3 — observability stack

**Trigger:** ALL of:

- `/metrics` has been collecting in prod for at least 2 weeks.
- Traffic is non-trivial — you can identify request patterns from the data.
- You've hit a regression or performance issue and reached for "I wish I
  had a dashboard" at least once.

**Adds:**

- **Grafana** (self-hosted or Grafana Cloud free tier).
- Dashboards: request rate, latency p50/p95/p99, DB pool stats, error rate
  by endpoint, business metrics (signups, listings created, transactions
  opened).
- Alerts: error rate, latency budget burn, DB connection saturation.
- **Distributed tracing** (OpenTelemetry → Tempo or Honeycomb) — only when
  you add a second service that calls the API.

**Why not earlier:** premature dashboards are dashboards of zero. You
can't tune alert thresholds without baseline data.

**Approximate effort:** 1–2 days.

### Phase 4 — Kubernetes + Helm

**Trigger:** ANY of:

- You hit Fly.io / PaaS limits on scale, networking flexibility, or cost.
- You want to demonstrate k8s on the portfolio explicitly (this is itself
  a valid trigger for a portfolio repo — but ship Phase 1–3 first).
- You add a third service that needs coordinated deployment with the
  backend.

**Adds:**

- `infra/k8s/` with manifests for `Deployment`, `Service`, `Ingress`,
  `ConfigMap`, `Secret` (sealed-secrets or external-secrets-operator).
- `infra/helm/` Helm chart parameterizing the manifests.
- The migration `fly.toml` → k8s manifests is documented in the repo as a
  portfolio piece in itself ("Phase 4: From PaaS to Kubernetes"). The
  migration is a feature, not a footnote.
- Cluster choice: **GKE Autopilot** (cheapest serverless k8s) or **EKS
  Fargate** (more "real cloud" for portfolio appeal).

**Why not earlier:** k8s for a single backend is theater. The migration
story is itself a portfolio signal that you understand *when* to scale up
complexity.

**Approximate effort:** 3–5 days.

### Phase 5 — multi-region, auto-scaling, advanced caching

**Trigger:** Measured bottleneck. Specifically:

- p95 latency exceeds budget for >5% of requests AND you can show it's
  geographic (CDN / multi-region helps).
- DB CPU sustained >70% under normal load (read replicas help).
- Single-region downtime is unacceptable for the SLA.

**Adds:**

- Postgres read replicas + router config in `app/db.py`.
- CDN with origin-shielding (CloudFlare / Cloud CDN / Bunny).
- Auto-scaling policies (HPA on k8s, or Fly.io autoscale rules).
- Multi-region deployment with failover.

**Why not earlier:** all of these are premature optimization until you
have data.

---

## Anti-patterns — explicitly NOT on the roadmap

These would be over-engineering for any plausible scale of this project.
If a future session is tempted to suggest one, push back:

- **Service mesh (Istio / Linkerd)** — single backend, irrelevant.
- **Microservice decomposition** — a single FastAPI service is fine for
  everything in `system_design.md`. Don't split until measured pain.
- **Custom Kubernetes operators** — solved problems; use community charts.
- **Manual blue/green DB migrations before staging exists** — the
  development cycle doc already mandates reversible migrations and split
  additive/cleanup deploys. That's enough until Phase 2.

---

## How this document stays current

Per the docs-as-part-of-done policy in
[`contributing.md`](./contributing.md#when-to-update-which-doc), when:

- A phase ships → update its section to reflect what was actually built
  (might differ from the plan). Move the "Approximate effort" estimate to
  "Actual effort" with the date.
- A trigger condition changes (e.g. you decide to skip Fly.io and go
  straight to GKE) → update the phase text and explain why.
- A new trigger emerges from operating the system → add it.
- **The CR subagent at [`.claude/agents/cr.md`](../.claude/agents/cr.md)
  must be updated in the same PR** so reviews enforce the new phase
  boundary or trigger.
