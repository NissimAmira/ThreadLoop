## Linked work

<!-- Required for non-trivial PRs. Trivial PRs (typo fix, lint, etc.) can strike this section through. -->

- **Closes:** #<task-id>
- **Refs (epic):** #<epic-id>
- **RFC:** docs/rfcs/<file>.md (if any)

## Summary

<!-- 1-3 bullets describing what this PR changes and why -->

## Acceptance criteria from the linked task

<!--
Copy the AC list from the linked task issue. Tick each as the PR addresses it.
The CR subagent compares this list against the diff — leaving an unticked
item without explanation will be flagged as must_fix.
-->

- [ ] ...

## Scope

- [ ] Backend
- [ ] Web
- [ ] Mobile
- [ ] Shared / contracts
- [ ] Infra / CI
- [ ] Docs

## Test plan

<!-- How was this verified? Include commands and expected results. -->

- [ ] `make test` passes locally
- [ ] Manual smoke check
- [ ] OpenAPI / shared types regenerated if API changed

## Documentation

<!--
Per docs/contributing.md → "Documentation is part of done", every PR keeps
docs in sync with the change. Tick the boxes that apply, OR strike through
the section with a one-line justification (e.g. "no doc impact: pure
refactor with no API/schema/architecture changes").
-->

- [ ] `shared/openapi.yaml` updated (any API surface change)
- [ ] `system_design.md` updated (API contracts / SQL schema change)
- [ ] Relevant `docs/<topic>.md` updated (auth/search/assets/architecture)
- [ ] `CLAUDE.md` updated (convention or what-not-to-do change)
- [ ] `README.md` roadmap ticked (user-visible feature shipped)
- [ ] No documentation impact (justify in summary)

## Checklist

- [ ] Conventional commit title (e.g. `feat:`, `fix:`, `chore:`)
- [ ] No secrets committed
- [ ] Migration is reversible (if DB changed)
