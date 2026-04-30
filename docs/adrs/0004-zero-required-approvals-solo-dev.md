# ADR 0004: Zero required approvals on `main`

- **Status:** Accepted
- **Date:** 2026-04-29
- **Context links:** `.github/branch-protection.md`

## Context

The branch-protection ruleset on `main` requires PRs (no direct pushes)
and three CI checks before merge. The original rule additionally
required 1 approving review.

GitHub does not allow PR authors to approve their own PRs. With a solo
developer, that turned every PR into a permanently-blocked one.

## Decision

Set `required_approving_review_count` to **0** on the `main`-protection
ruleset. Keep the PR-required rule and required CI checks. Conversation
resolution is still required — leaving an unresolved review comment
blocks merge.

## Consequences

- (+) Solo dev can actually merge their own PRs.
- (+) PR + CI gate still prevents direct pushes and broken code.
- (+) Conversation-resolution requirement gives a slot to enforce the
  CR subagent's findings.
- (−) Lower review bar than a multi-person team. Acceptable while solo;
  if a second contributor joins, raise to 1.

## Alternatives considered

**Bypass-actor allow list (admin self-merges).** GitHub supports
exempting specific actors from rules. Rejected because the user is the
only actor anyway and "0 required" is more honest than "required but
bypassed."

**Configure a self-approving bot.** Workflow that auto-approves the
author's own PR. Defeats the rule's intent and adds maintenance.
