# Branch protection — recommended settings

Apply these to `main` once CI runs successfully on the first PR. From the repo:
**Settings → Branches → Add rule → Branch name pattern: `main`**.

- ✅ Require a pull request before merging
  - ✅ Require approvals (1)
  - ✅ Dismiss stale reviews on new commits
- ✅ Require status checks to pass before merging
  - Required: `backend-test`
  - Required: `web-test`
  - Required: `mobile-test`
  - (These are the underlying status-check context names. The GitHub UI's
    "Checks" tab displays them as `Backend CI / backend-test` etc., but
    branch protection matches against the bare job-name context.)
- ✅ Require branches to be up to date before merging
- ✅ Require conversation resolution before merging
- ✅ Do not allow bypassing the above settings

The `release-please` and `README sync` workflows write to `main` directly via
the bot — keep "Allow GitHub Actions to create and approve pull requests"
enabled in **Settings → Actions → General**.
