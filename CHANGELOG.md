# Changelog

All notable changes to this project will be documented in this file.
Maintained automatically by [release-please](https://github.com/googleapis/release-please).

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
