# Branch protection — current setup

ThreadLoop uses a **repository ruleset** (the newer system) on `main`, not a
classic "branch protection rule". The ruleset is named `main-protection` and
enforces:

- ✅ Require a pull request before merging
  - **0 required approvals** — solo developer; GitHub does not allow PR
    authors to approve their own PRs, so a non-zero requirement would
    permanently block all merges. The "PR required" rule still prevents
    direct pushes to `main`, and CI gates merges. If a second contributor
    is ever added, raise this to 1.
  - ✅ Dismiss stale reviews on new commits (no-op at 0 approvals, kept for
    when this becomes >0)
- ✅ Require status checks to pass before merging
  - Required: `backend-test`
  - Required: `web-test`
  - Required: `mobile-test`
  - (These are the underlying status-check context names — the bare job-name
    from each workflow YAML. The GitHub UI's "Checks" tab displays them as
    `Backend CI / backend-test` etc., but the ruleset matches the bare
    context. Three workflows naming their job `test` collapsed into one
    ambiguous context — that's why each workflow's job has a unique id.)
- ✅ Require branches to be up to date before merging (`strict_required_status_checks_policy`)
- ✅ Require conversation resolution before merging
- ✅ Block deletion and non-fast-forward pushes
- ✅ Do not allow bypassing the above settings

## Workflow trigger gotchas

- CI workflows have **no `paths:` filters**. They run on every PR. Path
  filters previously caused doc-only and release-please PRs to skip CI
  entirely, which the ruleset reads as "required check missing" → permanently
  blocked. Cost is ~2 min CI per docs-only PR; worth it for predictability.
- `release-please` authenticates as a **GitHub App**, not `GITHUB_TOKEN`.
  See [`release-please-app-setup.md`](./release-please-app-setup.md).
- The repo also has **"Allow GitHub Actions to create and approve pull
  requests"** enabled in **Settings → Actions → General** — required so
  workflows can open PRs.
