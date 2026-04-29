# Branch protection — recommended settings

Apply these to `main` once CI runs successfully on the first PR. From the repo:
**Settings → Branches → Add rule → Branch name pattern: `main`**.

- ✅ Require a pull request before merging
  - ✅ Require approvals (1)
  - ✅ Dismiss stale reviews on new commits
- ✅ Require status checks to pass before merging
  - Required: `Backend CI / test`
  - Required: `Web CI / test`
  - Required: `Mobile CI / test`
- ✅ Require branches to be up to date before merging
- ✅ Require conversation resolution before merging
- ✅ Do not allow bypassing the above settings

The `release-please` and `README sync` workflows write to `main` directly via
the bot — keep "Allow GitHub Actions to create and approve pull requests"
enabled in **Settings → Actions → General**.
