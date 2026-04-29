# release-please GitHub App — one-time setup

Background: when `release-please` runs with `GITHUB_TOKEN` (the default), the
PR it opens is authored by `github-actions[bot]`. GitHub deliberately blocks
`GITHUB_TOKEN`-authored events from triggering further workflows, which means
the release PR never gets CI and is permanently blocked by branch protection.

The fix is to authenticate `release-please` as a **GitHub App** instead.
Tokens minted from a GitHub App are full first-class citizens — pushes and
PRs they create *do* trigger downstream workflows.

This file walks through the one-time setup. Estimated time: 5 min.

---

## 1. Create the GitHub App

1. Go to **https://github.com/settings/apps/new** (your personal account).
2. Fill in:
   - **GitHub App name**: `ThreadLoop Release Bot` (must be globally unique;
     append a suffix like `-nissim` if taken).
   - **Homepage URL**: any URL — `https://github.com/NissimAmira/ThreadLoop`
     is fine.
   - **Webhook**: ❌ **Uncheck "Active"** — we don't need webhooks.
3. **Repository permissions** (scroll down):
   - **Contents**: Read and write
   - **Pull requests**: Read and write
   - All others: leave at "No access"
4. **Where can this GitHub App be installed?** → "Only on this account".
5. Click **Create GitHub App** at the bottom.

## 2. Generate a private key

1. On the App's settings page, scroll to **Private keys** → click
   **Generate a private key**.
2. A `.pem` file downloads — keep it safe.
3. Note the **App ID** at the top of the page (a number, e.g. `123456`).

## 3. Install the App on the ThreadLoop repo

1. From the App's settings page, click **Install App** in the left sidebar.
2. Click **Install** next to your account.
3. Choose **Only select repositories** → pick `NissimAmira/ThreadLoop` →
   **Install**.

## 4. Add the secrets to the repo

Repo → **Settings → Secrets and variables → Actions → New repository secret**.

Add two secrets:

| Name | Value |
| --- | --- |
| `RELEASE_PLEASE_APP_ID` | The App ID from step 2 (e.g. `123456`) |
| `RELEASE_PLEASE_APP_PRIVATE_KEY` | The full contents of the `.pem` file from step 2, including the `-----BEGIN/END RSA PRIVATE KEY-----` lines |

## 5. Verify

Push any commit to `main` (e.g. merge a tiny PR). The `release-please`
workflow will run; if the App is set up correctly, it'll either:

- Open / update a release PR authored by **`ThreadLoop Release Bot`**
  (not `github-actions[bot]`), and that PR will get CI runs automatically.
- Or report "no changes need a release" — also a valid outcome.

If the workflow fails with `Bad credentials` or similar, double-check that
the secrets contain exactly what step 2 produced, and that the App is
**installed on this repo** (step 3, easy to forget).

---

## What if the secrets aren't set yet?

Then the `release-please` workflow's first step (`Generate App token`)
fails. The required CI checks on PRs (`backend-test`, `web-test`,
`mobile-test`) are unaffected because they run in different workflows. PRs
remain mergeable; only release PRs are blocked until the App is set up.

In other words: this PR is safe to merge before the App exists, but **no
further releases will be cut** until the secrets are added. Set up the App
the same day you merge this PR.
