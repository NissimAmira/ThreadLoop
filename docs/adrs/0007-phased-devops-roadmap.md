# ADR 0007: Phased DevOps roadmap with explicit triggers

- **Status:** Accepted
- **Date:** 2026-04-29
- **Context links:** `docs/devops-roadmap.md`

## Context

A "production-scalable grade" portfolio project needs deployment,
monitoring, orchestration, rollback, and multi-environment topology
**eventually**. The two failure modes are equal and opposite:

- **Too early:** scaffold k8s + Helm + Grafana on day one with no
  features yet → over-engineering, dead config, no actual signal.
- **Too late:** wait until production is on fire → emergency refactor
  under time pressure, learning the tools while bleeding.

Neither serves a portfolio piece (or a real product) well.

## Decision

Define a **phased roadmap** at `docs/devops-roadmap.md` with **explicit
triggers** for when each capability gets introduced. Five phases, each
with a concrete trigger condition, "why not earlier", "why not later",
and approximate effort. Currently at Phase 0 (local-only).

The roadmap doc is written to be **scanned by future Claude sessions**:
it explicitly instructs them to proactively prompt the user when a
trigger fires. Memory file `project_devops_roadmap.md` captures the
trigger scan so any session in this project picks it up automatically.

## Consequences

- (+) Each capability lands when its cost-of-not-having crosses the
  cost-of-adding threshold — neither premature nor reactive.
- (+) The phasing itself is a portfolio signal: it demonstrates the
  judgement of *when* to scale up complexity, which is more valuable
  than checking off all the capabilities at once.
- (+) Future sessions don't have to re-litigate the order — the doc is
  the source of truth.
- (−) The roadmap must stay current. New triggers, new phases, or
  changed approach require updating the doc + the CR subagent's rubric
  (per the keep-in-sync rule).
- (−) Discipline required: someone has to push back when a contributor
  proposes premature work ("let's add k8s now"). The CR subagent helps
  by flagging premature infra as `should_fix`.

## Alternatives considered

**Build everything up front.** "Production-grade" interpreted maximally
— k8s, Helm, Grafana, multi-region from PR #1. Rejected: dead config
without features to deploy, no real signal in the artifacts.

**Pure YAGNI — add things only when bleeding.** Cheapest in the short
term. Rejected: leads to crisis-driven engineering, missed lead time
for things like staging environments and DB backup drills.

**Implicit roadmap (in someone's head).** Common in solo projects.
Rejected: doesn't survive across sessions, can't be referenced by an
AI agent or a future contributor, defeats the portfolio aspect.
