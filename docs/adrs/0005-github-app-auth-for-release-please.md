# ADR 0005: GitHub App authentication for release-please

- **Status:** Accepted
- **Date:** 2026-04-29
- **Context links:** `.github/release-please-app-setup.md`,
  `.github/workflows/release-please.yml`

## Context

`release-please` opens release PRs based on conventional commit history.
Default authentication uses `GITHUB_TOKEN`. GitHub deliberately blocks
events triggered by `GITHUB_TOKEN` from triggering downstream workflow
runs — a guard against infinite loops.

The consequence: release PRs authored by `github-actions[bot]` never
get CI runs. The `main`-protection ruleset requires CI to pass for
merge. So every release PR was permanently un-mergeable.

We hit this on `v1.0.0` and worked around it with a manual
close-and-reopen, which re-attributes the events to the human. Every
future release PR would have hit the same wall.

## Decision

Authenticate `release-please` as a **GitHub App** (`ThreadLoop Release
Bot`) instead of `GITHUB_TOKEN`. The workflow uses
`actions/create-github-app-token@v1` to mint a short-lived token from
the App's private key (stored as repo secrets `RELEASE_PLEASE_APP_ID`
and `RELEASE_PLEASE_APP_PRIVATE_KEY`).

App-authored events DO trigger downstream workflows.

## Consequences

- (+) Release PRs get CI automatically; no human intervention required.
- (+) `release-please` is now genuinely hands-off as designed.
- (+) The pattern generalizes — any future bot that creates PRs we want
  CI'd uses the same approach.
- (−) One-time setup: create the App, install on the repo, add two
  secrets. Documented in `.github/release-please-app-setup.md`.
- (−) The App's private key is a secret to manage. No expiry unless we
  rotate it explicitly.

## Alternatives considered

**Personal Access Token (PAT).** Simpler — drop a PAT in a secret.
Rejected because PATs expire (max 1 year on fine-grained PATs), are
tied to a specific user, and grant access at that user's permission
level. GitHub Apps are the canonical solution.

**Manual close-and-reopen forever.** Free but tedious and error-prone;
the user would inevitably forget on a release that should have shipped.

**Different release tool that doesn't have this problem.** None of the
alternatives we evaluated (semantic-release, changesets) are
fundamentally different here — they all run as bots and hit the same
`GITHUB_TOKEN` constraint.
