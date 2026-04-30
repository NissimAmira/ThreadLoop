# ADR 0006: Local CR subagent instead of paid CI code reviewer

- **Status:** Accepted
- **Date:** 2026-04-29
- **Context links:** `.claude/agents/cr.md`

## Context

We considered an AI code reviewer that runs in CI on every PR — Claude
Opus 4.7 invoked via the Anthropic API, posting findings as PR comments.
Approximate cost: $0.05–0.20 per PR review.

The author's existing Claude Code subscription already provides
unlimited Claude usage at a flat rate. Running the same review locally
via a Claude Code subagent costs $0 incremental.

## Decision

Build the code reviewer as a **Claude Code subagent**
(`.claude/agents/cr.md`), invoked from the developer's local Claude
Code session, **not** as a CI workflow.

The subagent reads the relevant diff (working tree, branch, or specific
PR via `gh pr diff`), applies the ThreadLoop rubric, and posts findings
back to the chat — never to GitHub directly.

## Consequences

- (+) Zero per-PR cost.
- (+) Findings appear before the developer pushes, often catching
  issues that would otherwise round-trip through CI.
- (+) Subagent has access to the full repo context including dynamic
  reads of `CLAUDE.md` and `docs/contributing.md`.
- (−) Reviews don't run automatically — the developer must remember to
  invoke the agent.
- (−) Findings aren't visible in PR comments to a future portfolio
  reviewer (no public artifact of the review).
- (−) Quality is bounded by the developer remembering to invoke + the
  subagent's rubric being kept in sync (see ADR 0007 / the
  keep-in-sync rule in `docs/contributing.md`).

## Alternatives considered

**CodeRabbit free tier.** Free for public repos, runs in CI
automatically, posts comments on every PR. Functionally complementary
to the local agent rather than competing — adds visible PR comments.
**Deferred** as a nice-to-have addition: revisit if the local agent
proves insufficient in practice. Captured in
`memory/project_task_management_plan.md`.

**Anthropic API in CI (paid).** Same model, same quality, but
$0.05–0.20 per PR. Rejected — the user's existing Claude Code
subscription removes the cost justification.

**No AI reviewer at all.** Cheapest. Rejected — the docs-as-part-of-done
policy is hard to enforce manually with consistency, and the rubric
embodies non-trivial project knowledge worth automating.
