# ADR 0008: Multi-agent dev cycle simulating a real product team

- **Status:** Accepted
- **Date:** 2026-04-30
- **Context links:** `CLAUDE.md` § "How the dev cycle works",
  `.claude/agents/`, RFC 0001 (the first work driven through it)

## Context

The repo is a portfolio-grade solo project. A solo developer playing
all roles loses two things real teams have: separation of concerns
(product vs implementation vs review) and the artifacts those roles
produce (specs, breakdowns, reviews). Both improve quality.

Claude Code natively supports subagents (`.claude/agents/<name>.md`) —
isolated, role-specific agent configurations with their own system
prompts, tool allow-lists, and contexts. Combined with the
GitHub-Projects-based task management we set up alongside this ADR,
they can simulate a real product team's role separation.

## Decision

Define **six subagents** covering the dev cycle:

| Subagent | Role | Produces |
|---|---|---|
| `pm` | Product manager | RFC + Epic with user stories, AC, open questions |
| `tech-lead` | Tech lead | Sub-task breakdown under the Epic by area (BE/FE/Test/Infra/Docs); ADRs for big architectural decisions |
| `backend-dev` | Backend engineer | Branch + PR for `[BE]` sub-tasks |
| `web-dev` | Web frontend engineer | Branch + PR for `[FE-Web]` sub-tasks |
| `mobile-dev` | Mobile engineer | Branch + PR for `[FE-Mobile]` sub-tasks |
| `cr` | Code reviewer | Findings against rubric + linked-issue acceptance criteria |

The main Claude Code session orchestrates by invoking each subagent at
the appropriate phase. Each subagent runs in its own context window so
phase artifacts (e.g., a 1000-line implementation diff) don't pollute
the design conversation.

## Consequences

- (+) Each role's concerns are actually separate — `pm` doesn't draft
  technical breakdowns, `backend-dev` doesn't second-guess product
  scope, `cr` doesn't write code.
- (+) Artifacts (RFC, epic with AC, sub-task breakdown, PR review) are
  produced as a byproduct of the cycle, not as a chore on top of it.
- (+) Future Claude sessions in this repo pick up the same role
  separation automatically — the agents are checked-in config.
- (+) Portfolio signal: a reviewer sees `.claude/agents/` with six
  defined roles and immediately understands the engineering operation.
- (−) Six agent rubrics to keep current. Each new convention,
  schema constraint, or process change must update the relevant agent
  files. The `cr` rubric and `tech-lead`'s breakdown patterns are the
  most-touched.
- (−) Solo dev still ultimately drives every handoff — the agents
  don't independently decide to invoke each other. (Could be added
  later if useful.)

## Alternatives considered

**One generalist agent.** A single `dev` agent that handles design,
breakdown, implementation, and review. Rejected — it would have a giant
system prompt, blur role concerns, and pollute its context across
phases. Defeats the point of subagents.

**Just slash commands, no specialized agents.** `/design`, `/breakdown`,
`/implement`, `/review` as slash commands in the main session.
Rejected — no context isolation, and the rubric for each phase would
have to live in the main session. Subagents are the native pattern for
exactly this case.

**Skip `pm` and `tech-lead`, only have implementation + review agents.**
Lighter setup. Rejected — the design and breakdown phases produce the
artifacts that make the rest of the cycle work (RFCs, AC lists,
sub-tasks). Skipping them brings us back to the "solo dev plays all
roles" problem this ADR exists to solve.
