# Changelog

All notable changes to this project will be documented in this file.
Maintained automatically by [release-please](https://github.com/googleapis/release-please).

## [1.4.0](https://github.com/NissimAmira/ThreadLoop/compare/v1.3.0...v1.4.0) (2026-05-04)


### Features

* add AUTH_ENABLED feature flag, fix 501 contract drift (cr feedback) ([3f5ba76](https://github.com/NissimAmira/ThreadLoop/commit/3f5ba76ccf4a166a2eca45fb0809ebcb16fd3b3c))
* **backend:** add refresh_tokens table + RefreshToken model ([#22](https://github.com/NissimAmira/ThreadLoop/issues/22)) ([932af9c](https://github.com/NissimAmira/ThreadLoop/commit/932af9c7f219967e7d921128c1b3e67b899693f4)), closes [#11](https://github.com/NissimAmira/ThreadLoop/issues/11)
* **backend:** explicitly gate /api/me under AUTH_ENABLED + 404 on lifecycle routes ([4c07e8f](https://github.com/NissimAmira/ThreadLoop/commit/4c07e8f09b788e361262c78ce5828a4ddaf77b07))
* **backend:** implement /api/auth/apple/callback ([#15](https://github.com/NissimAmira/ThreadLoop/issues/15)) ([6ad9446](https://github.com/NissimAmira/ThreadLoop/commit/6ad9446ecf0aad87ba4891fa5ba46c9839446b36))
* **backend:** implement /api/auth/apple/callback ([#15](https://github.com/NissimAmira/ThreadLoop/issues/15)) ([0bb646e](https://github.com/NissimAmira/ThreadLoop/commit/0bb646e2b249d196f6c35e035d7759e858cf8645))
* **backend:** implement /api/auth/facebook/callback ([#16](https://github.com/NissimAmira/ThreadLoop/issues/16)) ([5d570ce](https://github.com/NissimAmira/ThreadLoop/commit/5d570ce27240b744fd858c2135efc48f9cd6ed92))
* **backend:** implement /api/auth/facebook/callback ([#16](https://github.com/NissimAmira/ThreadLoop/issues/16)) ([9d89eec](https://github.com/NissimAmira/ThreadLoop/commit/9d89eecc564af1e8d212c02a63e22abb2945ef5f)), closes [#11](https://github.com/NissimAmira/ThreadLoop/issues/11)
* **backend:** implement /api/auth/google/callback ([#14](https://github.com/NissimAmira/ThreadLoop/issues/14)) ([ac2c11b](https://github.com/NissimAmira/ThreadLoop/commit/ac2c11b78a39006dbd4bacb6c51f8d4cf85dbb03))
* **backend:** implement /api/auth/google/callback ([#14](https://github.com/NissimAmira/ThreadLoop/issues/14)) ([d714309](https://github.com/NissimAmira/ThreadLoop/commit/d714309715173aecbbfd6a95c9d3d07890bb4cb1)), closes [#11](https://github.com/NissimAmira/ThreadLoop/issues/11)
* **backend:** refresh_tokens table + RefreshToken model ([#22](https://github.com/NissimAmira/ThreadLoop/issues/22)) ([ec8b748](https://github.com/NissimAmira/ThreadLoop/commit/ec8b748954e5de5cb8d46f3d00fb34e16b1869ac))
* **backend:** session model + /api/me + Apple cache-key fix ([#17](https://github.com/NissimAmira/ThreadLoop/issues/17)) ([5aa2794](https://github.com/NissimAmira/ThreadLoop/commit/5aa279497b67b8c2eb648246d1675c93173cacd5))
* **backend:** session model + /api/me + Apple cache-key fix ([#17](https://github.com/NissimAmira/ThreadLoop/issues/17)) ([da4e86b](https://github.com/NissimAmira/ThreadLoop/commit/da4e86b97cd8deede3a9db246fac652fce0132c3))
* **backend:** validate /debug_token expires_at and cross-check user_id against /me ([44b8d29](https://github.com/NissimAmira/ThreadLoop/commit/44b8d2908f95b76ece9d38e0834e45215b907e0b))
* **shared:** define auth contract for SSO endpoints ([#12](https://github.com/NissimAmira/ThreadLoop/issues/12)) ([fcd4750](https://github.com/NissimAmira/ThreadLoop/commit/fcd47501303fef63342ff6c4b39917ddfabdbced))
* **shared:** define auth contract for SSO endpoints ([#12](https://github.com/NissimAmira/ThreadLoop/issues/12)) ([4ff0f19](https://github.com/NissimAmira/ThreadLoop/commit/4ff0f19f6561312a298d5de2fa60b32322ea3d82)), closes [#11](https://github.com/NissimAmira/ThreadLoop/issues/11)
* **web:** Apple sign-in button — slice 2 end-to-end ([#38](https://github.com/NissimAmira/ThreadLoop/issues/38)) ([#55](https://github.com/NissimAmira/ThreadLoop/issues/55)) ([b215d1f](https://github.com/NissimAmira/ThreadLoop/commit/b215d1f0c1971ead64beb41d22fceb0843d1a45e))
* **web:** Google sign-in slice 1 — /sign-in + /me end-to-end ([#19](https://github.com/NissimAmira/ThreadLoop/issues/19)) ([#43](https://github.com/NissimAmira/ThreadLoop/issues/43)) ([6ab9a52](https://github.com/NissimAmira/ThreadLoop/commit/6ab9a52aed17577c536ebaa1e296876146757d48))


### Bug Fixes

* **backend:** copy app + alembic into dev image before pip install ([#48](https://github.com/NissimAmira/ThreadLoop/issues/48)) ([7ff09ed](https://github.com/NissimAmira/ThreadLoop/commit/7ff09edfd40774e4891160658ac0f2ca10d5160a))
* **backend:** gate auth secrets per-provider, not globally ([#51](https://github.com/NissimAmira/ThreadLoop/issues/51)) ([#53](https://github.com/NissimAmira/ThreadLoop/issues/53)) ([c51b07a](https://github.com/NissimAmira/ThreadLoop/commit/c51b07abc45e49e016dacc527f9c5a2d496f97c9))
* **infra:** dev stack env forwarding + meilisearch healthcheck + Facebook env rename ([#50](https://github.com/NissimAmira/ThreadLoop/issues/50)) ([c4dbe35](https://github.com/NissimAmira/ThreadLoop/commit/c4dbe3574fb4ed49b519a5974027e999b24015a4))

## [1.3.0](https://github.com/NissimAmira/ThreadLoop/compare/v1.2.0...v1.3.0) (2026-04-30)


### Features

* task management + multi-agent dev cycle ([ee563b3](https://github.com/NissimAmira/ThreadLoop/commit/ee563b3b3e39bdaa3963cf5c6440b097619ae086))
* task management + multi-agent dev cycle ([2b77add](https://github.com/NissimAmira/ThreadLoop/commit/2b77add6b304c01c5a7284c3086610722338f7ed))

## [1.2.0](https://github.com/NissimAmira/ThreadLoop/compare/v1.1.0...v1.2.0) (2026-04-29)


### Features

* **devops:** production Dockerfiles + phased roadmap with explicit triggers ([9d01208](https://github.com/NissimAmira/ThreadLoop/commit/9d0120870c713b0537958284026b2fa0936e13d6))
* **devops:** production Dockerfiles + phased roadmap with triggers ([7895e81](https://github.com/NissimAmira/ThreadLoop/commit/7895e81f1221289861f8b5c3c3411a3eb13f2fe1))

## [1.1.0](https://github.com/NissimAmira/ThreadLoop/compare/v1.0.0...v1.1.0) (2026-04-29)


### Features

* add CR subagent and the rule that keeps it in sync ([9c16d7d](https://github.com/NissimAmira/ThreadLoop/commit/9c16d7dddeb94ceb15502543b49982a28258f14c))
* docs-as-part-of-done policy ([0fa5414](https://github.com/NissimAmira/ThreadLoop/commit/0fa5414f6da889444166795fcf36100bf4deab48))
* docs-as-part-of-done policy + CR subagent ([e3bceef](https://github.com/NissimAmira/ThreadLoop/commit/e3bceef2c172aa99195dd72740dbd9e02be74cf9))

## 1.0.0 (2026-04-29)


### Features

* scaffold ThreadLoop monorepo ([2f1fc13](https://github.com/NissimAmira/ThreadLoop/commit/2f1fc13996a44f49872dd4ec6daeb3e064076a50))
* scaffold ThreadLoop monorepo ([16a44af](https://github.com/NissimAmira/ThreadLoop/commit/16a44af3eaa35847c0f861d177b1eae31eda34d3))


### Bug Fixes

* **ci:** alembic env.py import order + vitest fake timers ([1d2431a](https://github.com/NissimAmira/ThreadLoop/commit/1d2431a3f084ac9652e20c06cd9e9330d1d60763))
* **ci:** backend ruff config + web tsconfig types ([1ded28d](https://github.com/NissimAmira/ThreadLoop/commit/1ded28deabf7c252aa810a0135c2827726410be3))
* **ci:** drop `paths` filters so required checks always run ([075ffc7](https://github.com/NissimAmira/ThreadLoop/commit/075ffc761ac9c7952b435f6bce8ea48f20d7b5ea))
* **ci:** drop paths filters so required checks always run ([ccf2b28](https://github.com/NissimAmira/ThreadLoop/commit/ccf2b28a345eff5422ef22a61b2abedcfcaf05b0))
* **ci:** give each workflow a unique job name for branch protection ([9699126](https://github.com/NissimAmira/ThreadLoop/commit/969912665256fe85d3315bc63bc4b23a988cc2a4))
* **ci:** give each workflow a unique job name for branch protection ([f7bba85](https://github.com/NissimAmira/ThreadLoop/commit/f7bba8563250f66d1a65a93deeb79db1524e3962))
* **ci:** reorder alembic/env.py imports for ruff isort ([3bbca60](https://github.com/NissimAmira/ThreadLoop/commit/3bbca607f79e5c37a4fecc319a0224014b92d47b))
* **ci:** tell ruff that `app` is first-party ([709d4c3](https://github.com/NissimAmira/ThreadLoop/commit/709d4c35beb1a8001b44e3c3ca61722209f13254))
* **ci:** unblock backend, web, and mobile pipelines ([e091865](https://github.com/NissimAmira/ThreadLoop/commit/e0918652fe4a5365e1d2a3b9623f23c9a4fd0b3a))

## 0.1.0

- Initial monorepo scaffold (backend, web, mobile, shared, infra).
- Health-check flow: API endpoint + web/mobile status indicator.
