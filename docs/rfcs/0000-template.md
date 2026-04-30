# RFC NNNN: <Title>

- **Status:** Draft | Under review | Approved | Rejected | Superseded by RFC NNNN
- **Author:** <name>
- **Created:** YYYY-MM-DD
- **Approved:** YYYY-MM-DD (or `—`)
- **Tracking issue:** #<epic-issue-number>

> RFCs are for **non-trivial** product or architectural changes — anything
> whose shape is debatable. Trivial features (a single endpoint, a CRUD
> resource, a UI tweak) don't need an RFC; just open an Epic with the
> requirements directly. The CR subagent flags non-trivial features that
> land without an RFC as `should_fix`.

## TL;DR

One paragraph. The change, the user-facing value, and the reason this
needs an RFC instead of just an Epic.

## Problem

What user need or system pain are we addressing? Include data, quotes, or
references where possible. Say what is *not* the problem (negative space).

## Goals

What this RFC must achieve, in order of priority. Each goal should be
verifiable.

## Non-goals

What this RFC explicitly does NOT cover, even though adjacent reviewers
might assume it would. Move "later" items here.

## Proposal

The recommended design. This is the meat of the RFC. Cover:

- User-visible behavior (flows, screens, API shape).
- Technical approach at a level where another engineer could begin
  implementation. Include diagrams where they help.
- Data model changes (new tables, new fields, migrations needed).
- Failure modes and how the system behaves under each.

## Alternatives considered

For each meaningful alternative, write a paragraph: what it is, what's
attractive about it, why we rejected it. **At least two alternatives**
unless the choice is genuinely forced.

## Risks and open questions

- Risks: what could go wrong, how we'd detect it, how we'd mitigate.
- Open questions: things that aren't decided yet, who needs to weigh in,
  by when.

## Rollout plan

How we ship this. Phased? Behind a feature flag? Migration order?
Consider:

- DB migration strategy (additive first, cleanup later).
- Backwards compatibility window.
- Documentation updates needed (per `docs/contributing.md`).
- Which `docs/devops-roadmap.md` phase this depends on (if any).

## Acceptance criteria

The verifiable list against which the implementation is judged. The
`tech-lead` subagent decomposes these into per-task acceptance criteria;
the `cr` subagent validates PRs against them.

- [ ] ...

## Out of scope follow-ups

What's worth doing after this RFC ships, captured here so it doesn't get
lost. Each becomes a separate Epic later.
