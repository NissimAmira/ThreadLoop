# CLAUDE.md

Quick-reference for Claude (and humans) working on ThreadLoop. Keep this file
**short and load-bearing** — push detail into [`docs/`](./docs) instead.

## Starting a new session — read these first, in order

1. **This file** — orientation, conventions, what NOT to do.
2. **`README.md`** — current roadmap with checkboxes (what's done, what's next).
3. **`git log --oneline origin/main -20`** — what just shipped.
4. **`gh pr list`** — anything in flight.
5. The relevant **`docs/<topic>.md`** for whatever you're touching.

If those four shell commands return nothing surprising, the repo is in a
**clean state** — pick the next unchecked item from the README roadmap and
branch off `main`.

> The session-start surface above is only useful if the previous Epic
> left it accurate. The symmetric rule on the way out is in
> [`§ Ending an Epic — session handoff`](#ending-an-epic--session-handoff)
> below; the `cr` agent enforces it on Epic-closing PRs.

## What this repo is

A peer-to-peer second-hand fashion marketplace with AR try-on, structured as a
monorepo. Every user is both buyer and seller. SSO-only authentication
(Google / Apple / Facebook), no passwords.

## What's actually built vs designed

The infrastructure setup is complete and `v1.2.0` shipped: monorepo scaffold,
health-check flow, CI gating, branch protection, release-please with a
GitHub App, production Dockerfiles, phased DevOps roadmap, docs-as-part-of-done
policy, local CR subagent, **task management via GitHub Projects + RFC/ADR
conventions, and a multi-agent dev cycle simulating a real product team** (this
is the most recent addition — see "How the dev cycle works" below).

The product features (auth, listings, search, transactions, AR viewer) have
**schemas and design docs but no implementation yet** — each domain doc has a
"What's not implemented yet" section that calls this out. The next planned
workstream is `feat/auth-sso` (RFC at `docs/rfcs/0001-auth-sso.md`), which
will be the first real feature driven through the multi-agent cycle.

## How the dev cycle works

ThreadLoop simulates a real product team's role separation through eight
specialized Claude Code subagents in [`.claude/agents/`](./.claude/agents).
Each agent has a role-specific system prompt, a tool allow-list, and runs in
its own context window so phase artifacts don't pollute each other.

```
                  IDEA / problem statement
                             │
                        invoke pm
                             │
              docs/rfcs/NNNN.md + Epic issue
                  (user stories, AC, open Qs)
                             │
                ┌────────────┴────────────┐
                ▼                         ▼
          invoke biz-dev          invoke ux-designer
        (ROI + funnel +          (flow sketch + a11y +
         market research)         AR ≤3 clicks rule)
                │                         │
                └────────────┬────────────┘
                             ▼
              advisory comments on Epic
              (pm revises or human overrides;
               human is expected to resolve
               before invoking tech-lead)
                             │
                     invoke tech-lead
                             │
             sub-issues under the Epic, by area
             [BE] / [FE-Web] / [FE-Mobile]
             [Test] / [Infra] / [Docs]
             (each with concrete AC, deps, risks)
                             │
                ┌────────────┴────────────┐
                ▼                         ▼
          invoke biz-dev          invoke ux-designer
        (cost-vs-value of         (flow sketch on FE
         each slice; slice 1       tasks before code;
         must unlock a demo)       call out friction)
                │                         │
                └────────────┬────────────┘
                             ▼
              advisory comments on tasks
              (tech-lead revises or escalates;
               dev agents read these before
               opening a branch)
                             │
         ┌──────────┬────────┴────────┬──────────┐
         ▼          ▼                 ▼          ▼
    backend-dev  web-dev         mobile-dev    ...
      PR #X       PR #Y            PR #Z
         │          │                 │
         │          └────────┬────────┘
         │                   ▼
         │            invoke ux-designer
         │            (FE PR UI review)
         │                   │
         └──────────┬────────┘
                    ▼
                invoke cr
   (rubric + linked-task AC validation +
    surfaces unaddressed biz-dev / ux-designer
    pushback as must_fix)
                    │
                 merge → ship
                    │
         release-please cuts vN.M.K
```

The main Claude Code session orchestrates: you say *"have the pm agent
design SSO sign-in"*, then *"have biz-dev review epic #N"*, then *"have
ux-designer review epic #N"*, then *"have the tech-lead break down epic #N"*,
then *"have ux-designer review the FE tasks for #N"*, then *"have web-dev
implement task #N+3"*, then *"have the cr agent review the current changes"*.
Each subagent reads `CLAUDE.md` + `docs/contributing.md` fresh on every
invocation.

| Subagent | Role | Inputs | Outputs |
|---|---|---|---|
| [`pm`](./.claude/agents/pm.md) | Product manager | Feature idea / problem statement | RFC in `docs/rfcs/` + Epic GitHub issue (user stories, AC, open questions) |
| [`biz-dev`](./.claude/agents/biz-dev.md) | Biz-dev / strategy advisor | Epic, tech-lead breakdown, or in-flight PR | Advisory comment: ROI, funnel impact, market context, scope-creep flags |
| [`ux-designer`](./.claude/agents/ux-designer.md) | UX/UI designer | Epic, FE task AC, or FE PR | Advisory comment: flow sketch, friction & a11y issues, AR-3-clicks check |
| [`tech-lead`](./.claude/agents/tech-lead.md) | Tech lead | An approved Epic | Sub-issues under the Epic by area, with per-task AC, deps, risks; ADRs for architectural choices |
| [`backend-dev`](./.claude/agents/backend-dev.md) | Backend engineer | A `[BE]` task | Branch + PR (FastAPI / SQLAlchemy / Alembic) |
| [`web-dev`](./.claude/agents/web-dev.md) | Web engineer | A `[FE-Web]` task | Branch + PR (Vite / React / TS / Tailwind) |
| [`mobile-dev`](./.claude/agents/mobile-dev.md) | Mobile engineer | A `[FE-Mobile]` task | Branch + PR (Expo / RN / TS) |
| [`cr`](./.claude/agents/cr.md) | Code reviewer | An open PR | Findings against rubric + AC validation + unaddressed advisory pushback |

### How agents push back on each other

Like a real dev team, every agent is expected to **push back when an
upstream artifact violates a load-bearing rule** — not just rubber-stamp
the previous step. The advisory agents (`biz-dev`, `ux-designer`) are
**not gates**: they have no `Write`/`Edit` tools and post comments only.
The single automated enforcement seam is `cr`'s Step 2.6, which scans
the linked task / Epic / PR at review time and surfaces any unaddressed
`[<agent> pushback]` markers as `must_fix`. Everywhere else in the
flow, the human is the gate — they read advisory comments and decide
whether to revise the upstream artifact or proceed.

Pushback is concrete, citable, and resolvable:

- It cites the specific rule, doc, AC text, or contract field that's
  being violated. Vibes-only objections aren't pushback.
- It proposes a resolution path: revise the upstream artifact, escalate
  to the human, or accept-with-justification.
- It's posted on the relevant GitHub issue/PR (so it's durable across
  agent invocations) and summarised in the chat output.
- The downstream agent **does not silently work around it** — they
  either wait for resolution or note in their own output that they
  proceeded over an unresolved pushback.

The `cr` agent enforces this loop: when it reviews a PR, it scans the
linked task and parent Epic for `[biz-dev pushback]` and
`[ux-designer pushback]` comments and surfaces unaddressed ones as
`must_fix`. Each agent's `## Push back when…` section in its own md file
lists the concrete triggers — read those for the canonical list.

**Keeping the cycle in sync** is part of the docs-as-part-of-done policy.
When you change a convention, schema constraint, or process rule, update the
relevant agent rubric in the same PR — agents are not auto-synced. The
[`cr`](./.claude/agents/cr.md) agent enforces this by flagging missing
agent updates.

## Ending an Epic — session handoff

The orientation files listed in [§ Starting a new session](#starting-a-new-session--read-these-first-in-order)
are only useful if the previous Epic left them accurate. **The closing
PR of an Epic — the one that satisfies the last unchecked AC — must
update the orientation surface in the same PR.** This is a hard rule,
enforced by the `cr` agent on Epic-closing PRs (see `cr.md` Step 2.7).

A new Claude Code session, started cold the day after an Epic closes,
should be able to:

1. Read `CLAUDE.md` and find the new built state in "What's actually
   built vs designed."
2. Read `README.md` and find the Epic's roadmap line ticked.
3. Read the relevant `docs/<topic>.md` and find the shipped behaviour
   under "What's built" rather than "What's not implemented yet."
4. Read `system_design.md` and find the schema / API surface
   matching what's deployed.
5. Read `docs/rfcs/NNNN-<slug>.md` and see it marked **Implemented**.

If any of those is stale, the handoff failed and the next session
will trip over it. The closing PR's checklist:

- [ ] **README roadmap** — line item ticked. (Roadmap automation reads
  commit messages, but hand-verify on the merged commit.)
- [ ] **`CLAUDE.md` "What's actually built vs designed"** — paragraph
  reflects the new built state if the Epic shipped a major feature.
- [ ] **Domain doc(s)** (`docs/auth.md` / `search.md` / `assets.md`) —
  shipped items moved out of "What's not implemented yet" into the
  built sections.
- [ ] **`system_design.md`** — schema + API surface match what shipped.
- [ ] **`docs/repository-structure.md`** — any new folders / workspaces
  / packages described.
- [ ] **RFC status line** — set to **Implemented** (or
  **Partially implemented** with the deferred items called out and a
  follow-up Epic referenced).
- [ ] **ADRs for any mid-cycle decisions** that weren't in the
  original `tech-lead` breakdown.
- [ ] **release-please PR** — per the per-Epic cadence rule
  (`docs/contributing.md` § "Releases"), merge the held-open
  release-please PR for this Epic, or align it with the next one if
  Epics are bundling.

This is symmetric with the start-of-session reads: every file a fresh
session opens at boot is a file the closing PR must keep accurate.

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
- [`docs/auth.md`](./docs/auth.md) — SSO design, account linking, dual-role buyer/seller.
- [`docs/search.md`](./docs/search.md) — Meilisearch interface and swap path.
- [`docs/assets.md`](./docs/assets.md) — image and AR/.glb pipelines.
- [`.github/branch-protection.md`](./.github/branch-protection.md) — branch ruleset + why 0 approvals.
- [`.github/release-please-app-setup.md`](./.github/release-please-app-setup.md) — release-please GitHub App setup (why and how).
- [`.claude/agents/`](./.claude/agents/) — eight dev-cycle subagents (`pm`, `biz-dev`, `ux-designer`, `tech-lead`, `backend-dev`, `web-dev`, `mobile-dev`, `cr`). See "How the dev cycle works" above.
- [`docs/devops-roadmap.md`](./docs/devops-roadmap.md) — phased deployment/observability/orchestration plan with explicit triggers. **Scan it at session start** if any topic in the conversation touches deployment, infrastructure, performance, or observability — proactively prompt the user when a trigger has fired.
- [`docs/rfcs/`](./docs/rfcs/) — design proposals for non-trivial product/architectural changes. Numbered, append-only.
- [`docs/adrs/`](./docs/adrs/) — Architecture Decision Records. Short, focused "we decided X because Y" entries.

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
- **Documentation is part of "done".** Every PR must keep docs in sync with the change. The full list of which doc updates which kind of change lives in [`docs/contributing.md`](./docs/contributing.md#documentation-is-part-of-done). The PR template has a checkbox; reviewers and the CR subagent block on doc drift.
- **CR subagent ([`.claude/agents/cr.md`](./.claude/agents/cr.md)) is the local code reviewer.** Invoke it from any Claude Code session: *"review the current changes"* or *"have the cr agent review PR 42"*. It uses your Claude Code subscription (no API charges) and runs in its own context so big diffs don't pollute your main session. **When you introduce a new convention, schema constraint, or enforcement rule anywhere in the repo, update `.claude/agents/cr.md` in the same PR** — its rubric is baked in, not auto-synced.
- **DevOps phasing.** Cloud deployment, monitoring, k8s, and rollback capabilities are deliberately phased — see [`docs/devops-roadmap.md`](./docs/devops-roadmap.md). Each phase has a concrete trigger; do not introduce a phase before its trigger fires. Conversely, **proactively prompt the user when you observe a trigger has fired** — they may not notice on their own.
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
