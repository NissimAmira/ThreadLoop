# Development cycle

ThreadLoop follows a trunk-based branching model with three environments:
**Dev → Test → Prod**. Every change moves left to right; nothing skips a stage.

## Lifecycle at a glance

```
┌───────────┐  PR     ┌───────────┐  merge to main  ┌───────────┐  tag v*  ┌───────────┐
│   feat/X  │────────▶│   main    │────────────────▶│  staging  │─────────▶│    prod   │
│  (local)  │  CI     │           │   auto-deploy   │  (Test)   │  promote │           │
└───────────┘         └───────────┘                 └───────────┘          └───────────┘
   Dev                                                                        
```

## Phase 1 — Dev (local)

- **Branch from `main`.** Naming: `feat/<topic>`, `fix/<topic>`, `chore/<topic>`,
  `docs/<topic>`. Short-lived (target: hours, not days).
- **Run the stack** with `make dev`. The web app shows a live status pill
  polling `/api/health`.
- **Iterate** with hot reload on backend (uvicorn `--reload`), web (Vite HMR),
  and mobile (Expo Fast Refresh).
- **Add tests** alongside changes. Backend tests run against Testcontainers
  Postgres; web tests run in jsdom; Cypress runs against the live compose stack.
- **Commit early, commit often** — `release-please` parses the messages.

### Commit conventions (Conventional Commits)

| Prefix | Purpose | Bumps version? |
| --- | --- | --- |
| `feat:` | New user-visible feature | minor |
| `fix:` | Bug fix | patch |
| `perf:` | Performance change | patch |
| `refactor:` | Internal restructure, no behavior change | none |
| `chore:` | Tooling / deps | none |
| `docs:` | Documentation only | none |
| `test:` | Tests only | none |
| `feat!:` or `BREAKING CHANGE:` in body | Breaking API change | major |

Examples:
```
feat(listings): add cursor pagination to GET /listings
fix(auth): handle Apple "hide my email" relay addresses
chore(deps): bump fastapi to 0.115.5
```

## Phase 2 — Test (CI + Staging)

### CI (runs on every PR)

| Pipeline | Triggers when changes touch | Runs |
| --- | --- | --- |
| Backend CI | every PR | Ruff lint, mypy, Alembic migrations against fresh Postgres, Pytest with coverage, Schemathesis (planned) |
| Web CI | every PR | tsc, ESLint, Vitest, production build |
| Mobile CI | every PR | tsc, Jest |

### Where prod, staging, and the rest of the deployment story live

The Dev → Test flow above describes what runs on every PR. The
deployment side (cloud target, monitoring stack, orchestration, rollback
strategy, multi-environment promotion) follows a **phased roadmap with
explicit triggers** — see [`devops-roadmap.md`](./devops-roadmap.md). Do
not introduce a phase before its trigger fires; conversely, the doc is
written to be scanned every time a deployment / infrastructure / perf
topic comes up so triggers are surfaced promptly.

### The multi-agent dev cycle

Eight subagents in [`.claude/agents/`](../.claude/agents/) cover the dev
cycle: `pm` (design), `biz-dev` and `ux-designer` (advisory — ROI/funnel
and UX/a11y respectively), `tech-lead` (decompose), `backend-dev` /
`web-dev` / `mobile-dev` (implement), and `cr` (review). Each agent has
a `## Push back when…` section with concrete cite-a-rule triggers, so
the loop is self-policing — `cr` surfaces unaddressed advisory pushback
on the linked task / Epic / PR as `must_fix`. The full flow with
artifacts is documented in
[`CLAUDE.md` → "How the dev cycle works"](../CLAUDE.md#how-the-dev-cycle-works).

Each subagent runs in its own context window. The main Claude Code
session orchestrates by invoking the right one for the phase you're in.

### Local code review (`cr` subagent)

The `cr` agent at [`.claude/agents/cr.md`](../.claude/agents/cr.md) is
the local code reviewer. Invoke it from any Claude Code session — *"review
the current changes"* or *"have the cr agent review PR 42"*. It runs
against your Claude Code subscription (no per-PR API cost) and produces a
structured review with three severity buckets (`must_fix` / `should_fix`
/ `recommend`) **plus AC validation** against the linked task.

It is **not** wired into CI — by design, since it relies on a developer's
local Claude Code session. Use it before pushing or before requesting
human review.

**Keeping the dev-cycle agents in sync:** the agents have rubrics and
project conventions baked into their system prompts — not auto-synced.
Whenever you introduce a new convention, schema constraint, guideline,
or enforcement rule, update the relevant agent file (typically `cr.md`,
sometimes also a role agent like `backend-dev.md`) in the same PR. The
documentation table in
[`docs/contributing.md`](./contributing.md#when-to-update-which-doc)
includes rows for this; the `cr` agent itself flags PRs that touch any
agent file as a meta-change.

**Required to merge:**
- All triggered pipelines green
- 1 reviewer approval
- Branch up-to-date with `main`
- All review conversations resolved

See [`.github/branch-protection.md`](../.github/branch-protection.md) to apply
these rules.

### Staging (auto-deploy on merge to `main`)

- Mirrors prod infrastructure as closely as possible.
- Anonymized snapshot of prod data, refreshed nightly.
- Used for **integration testing** (full Cypress suite nightly), **load
  testing** (k6 pre-release), and **manual QA** before promoting to prod.

## Phase 3 — Prod (release)

### Release-please

After every merge to `main`, the `release-please` workflow opens (or updates)
a release PR. The PR:

- Bumps the version in `package.json` files according to commit prefixes since
  the last release.
- Regenerates `CHANGELOG.md` from commit messages.
- When merged, **tags** the commit `vX.Y.Z` and creates a GitHub Release.

The tag triggers the **prod deployment** workflow (blue/green on the prod
cluster, runs DB migrations, smoke-tests `/api/health`, flips traffic).

`release-please` authenticates as a **GitHub App** (`ThreadLoop Release Bot`),
not `GITHUB_TOKEN`. This is required so that the release PRs it opens get CI
runs — `GITHUB_TOKEN`-authored events deliberately don't trigger downstream
workflows. See [`.github/release-please-app-setup.md`](../.github/release-please-app-setup.md)
for the one-time App setup if you ever need to recreate it.

### Rollback

Two options, in order of preference:

1. **Revert the offending PR**, merge, let release-please cut a patch release.
2. For an emergency, **roll the prod cluster back to the previous tag** (the
   blue/green slot is still warm for ~1 hour after promotion).

Database migrations must be reversible — every Alembic revision implements
`downgrade()`. We never deploy a backward-incompatible migration with the same
release that depends on it; instead, we ship the migration first, deploy the
new code, then deploy the cleanup migration in a follow-up release.

## Quality gates summary

| Phase | Checks |
| --- | --- |
| Dev (local) | `make lint`, `make test`, `make health` |
| Test (CI) | Lint, type-check, unit tests, contract tests, build |
| Test (staging) | Full Cypress, k6 load test, manual QA, security scans |
| Prod | Health checks, error budget, rollback verified |

## How feature work flows end-to-end

1. **Pick up an issue / milestone item.**
2. `git checkout -b feat/<topic>` from latest `main`.
3. **Update the contract first** if API-shaped: edit `shared/openapi.yaml` and
   the matching TS types.
4. Implement backend → write/update Alembic migration → tests.
5. Implement frontend(s) consuming the new endpoints.
6. `make test` until green.
7. Push and `gh pr create`. CI runs.
8. Reviewer approves; merge to `main`.
9. Auto-deploy to staging; smoke-check.
10. release-please opens the release PR; merge when ready to ship.
11. Tag deploys to prod; verify `/api/health` and key user flows.

If any step blocks the next, **don't skip it** — fix the underlying problem.
