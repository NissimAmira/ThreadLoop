# Architecture Decision Records

Short, focused records of architectural choices: **what we decided, what
we considered, and why we chose this**. Each ADR is a single decision
captured in a few hundred words.

## When to write an ADR vs an RFC

| | ADR | RFC |
|---|---|---|
| Length | ~50–150 lines | Long-form, often 200+ |
| Trigger | A non-obvious decision was made | A non-trivial **feature** was proposed |
| Audience | "Why is this the way it is?" | "Should we do this?" |
| Lifespan | Permanent record of historical reasoning | Lives until shipped, then becomes archival |
| Lives in | `docs/adrs/NNNN-<slug>.md` | `docs/rfcs/NNNN-<slug>.md` |

Some changes produce both: the RFC proposes the feature, the ADR
captures one or more architectural choices made inside it (e.g. "we
chose JWT over session cookies"). The RFC links to the ADR(s).

## When NOT to write an ADR

- Choices fully derivable from current code or docs.
- Style preferences, formatter settings.
- Bug fixes.
- Routine library upgrades.

If a future engineer would have to ask *"why did we do it this way?"*,
write the ADR.

## Numbering

Sequential: `0001-meilisearch-from-day-one.md`, `0002-...`. Numbers are
permanent — rejected/superseded ADRs stay in place with status updated.

## Template

See [`0000-template.md`](./0000-template.md).
