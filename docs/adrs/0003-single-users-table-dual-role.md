# ADR 0003: Single `users` table for buyer/seller dual role

- **Status:** Accepted
- **Date:** 2026-04-29
- **Context links:** `system_design.md`, `docs/auth.md`

## Context

Every ThreadLoop user can both buy and sell. Two modeling options:

1. Separate `buyers` and `sellers` tables, with a join when one user
   holds both roles.
2. A single `users` table with capability flags (`can_sell`,
   `can_purchase`).

Option 1 reflects the "two distinct entities" framing; option 2 reflects
"one person, multiple capabilities."

## Decision

Single `users` table with capability flags. The `transactions` table
references `buyer_id` and `seller_id` from this same table, with a
`CHECK (buyer_id <> seller_id)` constraint preventing self-purchase.

## Consequences

- (+) Switching context (buyer ↔ seller) is a permission check, not a
  re-authentication.
- (+) Profile, reviews, account settings all live on one row.
- (+) One identity per person — no risk of someone listing as one
  account and buying as another to game the system.
- (+) Authorization is per-action (`can_sell`, `can_purchase`), not
  per-account-type — simpler to reason about.
- (−) Some queries that "only care about sellers" pay an extra filter.
  Negligible at any plausible scale.
- (−) Future role expansion (admin, moderator) probably wants a separate
  `roles` table instead of more flags. Acceptable; cross that bridge
  when needed.

## Alternatives considered

**Separate tables, foreign-key-joined when both apply.** Cleanly models
"distinct entities" but creates the case where one person has two
unrelated rows. Reviews-on-sellers vs reviews-on-buyers split awkwardly.
Cross-role analytics ("repeat sellers who also buy") become joins.

**Single table, single role flag (an enum).** Forces a user to pick.
Defeats the marketplace dynamic where browsing and listing are the
same activity.
