# RFCs

Design proposals for non-trivial product or architectural changes.

## When to write one

You should open an RFC when:

- The change is user-visible and has multiple plausible designs.
- An architectural choice would be hard to reverse later.
- Multiple subsystems are affected (backend + frontend + infra).
- A reviewer would reasonably ask *"why did we do it this way?"* a year
  from now.

You should NOT open an RFC for:

- A bug fix.
- A single new endpoint with an obvious shape.
- A pure refactor with no behavior change.
- Adding a CRUD resource that follows existing patterns.

When in doubt, ask the `pm` subagent — it'll either produce an RFC draft
or push back saying "this is just an Epic, here's the issue body."

## How they relate to Epics

| | RFC | Epic |
|---|---|---|
| Lives in | `docs/rfcs/NNNN-<slug>.md` (markdown file in repo) | GitHub Issue (with `type:epic` label) |
| Purpose | Long-form proposal + alternatives + rationale | Tracking + sub-issues + project planning |
| Audience | Anyone reviewing the design choice later | The work itself |
| Lifespan | Permanent (the historical record) | Closes when shipped |

A non-trivial epic links to its RFC. The RFC links to its tracking epic
issue. They co-exist.

## Numbering

RFCs are numbered sequentially: `0001-auth-sso.md`, `0002-foo.md`, etc.
The number is allocated when you create the file. Do not reuse numbers
even for rejected RFCs — leave the rejected file in place with status
`Rejected` so the historical reasoning survives.

## Process

1. Copy `0000-template.md` to `NNNN-<slug>.md` and fill it in.
2. Open an RFC issue using the `rfc.yml` template, linking the file.
3. Discussion happens in the issue.
4. When approved, update the RFC's `Status` to `Approved` and the
   `Approved` date.
5. Open the corresponding Epic issue (link both ways).
6. The `tech-lead` subagent decomposes the Epic's acceptance criteria
   into sub-tasks.
