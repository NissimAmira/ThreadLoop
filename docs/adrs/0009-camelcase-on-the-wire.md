# ADR 0009: camelCase on the wire (Pydantic alias_generator + populate_by_name)

- **Status:** Accepted
- **Date:** 2026-05-03
- **Context links:** Epic #11 (auth-sso), Issue #44 (snake↔camel drift),
  PR #43 (slice-1 web adapter that surfaced the cost), `shared/openapi.yaml`,
  `shared/src/types/`, `backend/app/auth/schemas.py`

## Context

The TS types in `shared/src/types/` were authored in camelCase (`displayName`,
`accessToken`, `linkRequired`, `priceCents`, `arAsset`, `glbLowUrl`,
`createdAt`). `shared/openapi.yaml` was authored in snake_case
(`display_name`, `access_token`, `link_required`, `price_cents`, `ar_asset`,
`glb_low_url`, `created_at`). Both shipped together in #12 — the divergence
was inherited from the original spec, not introduced later.

PR #43 (slice-1 Google sign-in on web) was the first place the cost showed up.
It handled the divergence with a hand-rolled per-endpoint adapter at the API
boundary (`frontend-web/src/api/client.ts`). The PR's own scope note flagged
this as not-for-this-slice and opened #44 to resolve it before slices 2–5
copy the pattern.

The four upcoming slices that would otherwise each ship their own adapter:

- **#38** (slice 2 — Apple end-to-end on web): consumes the same `Session` /
  `User` envelopes from a second callback.
- **#39** (slice 3 — Facebook end-to-end on web): same.
- **#40** (slice 4 — link_required UI, paired with #18): adds `POST
  /api/auth/link` and a fresh request body shape.
- **#20** (slice 5 — mobile sign-in): consumes the same shapes from RN.

Without resolving the drift, that's four more per-endpoint adapters and a
parallel mobile adapter — the duplication compounds. The failure mode is
silent: a misnamed field round-trips as `undefined` and surfaces as a UI
bug rather than a type error. The adapter in PR #43 deliberately did the
mapping by hand for exactly this reason — a generic recursive snake↔camel
converter loses type safety at the seam.

## Decision

**Make camelCase the wire shape.** Specifically:

1. Configure every Pydantic v2 wire model in `backend/app/auth/schemas.py`
   (and any future request/response models) with:

   ```python
   from pydantic import BaseModel, ConfigDict
   from pydantic.alias_generators import to_camel

   class WireBase(BaseModel):
       model_config = ConfigDict(
           alias_generator=to_camel,
           populate_by_name=True,
           serialize_by_alias=True,
       )
   ```

   `alias_generator=to_camel` produces camelCase aliases from snake-case
   Python attributes. `populate_by_name=True` keeps inbound parsing
   accepting either form (so internal callers using the Python name keep
   working). `serialize_by_alias=True` makes the response use the camelCase
   alias by default — without it, FastAPI serializes by attribute name.

2. Regenerate `shared/openapi.yaml` so every property name and `required:`
   entry reflects camelCase (`displayName` not `display_name`,
   `accessToken` not `access_token`, etc.). The TS types in
   `shared/src/types/` already match this — no change needed there.

3. Delete the per-endpoint adapter in `frontend-web/src/api/client.ts`
   (`UserWire` / `SessionWire` / `userFromWire` / `sessionFromWire`). The
   `request<T>()` helper returns the typed shape directly; no mapping at
   the boundary.

The error envelope is part of the change too: `request_id` becomes
`requestId` to match the existing TS `ApiError.requestId` field; the
client's `err.request_id` access becomes `err.requestId`.

## Consequences

- **(+)** Web (and mobile, when slice 5 lands) consume the typed shapes
  from `@threadloop/shared` directly — no per-endpoint adapter, no risk
  of a drifted field rounding to `undefined`.
- **(+)** The cross-cutting `[Test] / [Docs]` work that would have
  followed every new slice (re-implement an adapter, write its tests,
  document it) is eliminated for slices 2–5.
- **(+)** One source of truth: the Pydantic model name **is** the alias's
  source, the OpenAPI property is the alias, the TS field is the property.
  Drift is now a single-file edit away in either direction.
- **(+)** Reversible: dropping the `alias_generator` config + regenerating
  `openapi.yaml` flips back to snake-on-the-wire. Not a one-way door.
- **(−)** Wire shape is non-idiomatic JSON (camelCase JSON keys are common
  enough but snake is more common in Python-backed APIs). Acceptable
  trade-off because every consumer is a typed TS client.
- **(−)** If a future external integration *requires* snake on the wire,
  we'd either revert this ADR or expose a separate snake-aliased endpoint
  group. The cost is hypothetical today (no external consumers exist).
- **(−)** Pydantic's `populate_by_name=True` means inbound requests accept
  *either* form. That's deliberate (so internal callers and integration
  tests can use the Python name) but it does mean the wire contract is
  technically "camelCase, but snake also accepted" — a Schemathesis run
  against the regenerated openapi.yaml will only exercise the camel form,
  which is the documented contract.

## Alternatives considered

**Option B — Make the TS types snake_case to match `openapi.yaml`.** The
shared TS types in `shared/src/types/` would be rewritten so every field is
snake_case (`displayName` → `display_name`, etc.). Either hand-rewritten or
regenerated from `openapi.yaml` via `openapi-typescript`. Rejected because
(a) every existing web consumer (`AuthContext`, `AppHeader`, `MePage`, the
adapter itself, and all their tests) was authored in camelCase in slice 1
and would have to be rewritten in lockstep, (b) snake_case identifiers are
unidiomatic in TS — every component would read `user.display_name` instead
of `user.displayName`, and (c) the readability loss is permanent across
every future TS consumer for the lifetime of the project, while Option A's
backend churn is a one-time five-model edit.

**Option C — Codegen camelCase TS from snake `openapi.yaml`.** Add
`openapi-typescript` (or similar) with a transform that renames properties
to camelCase at generation time, replacing the hand-authored types in
`shared/src/types/`. Rejected because (a) the generated types describe the
camel shape, but the wire is still snake — so the runtime adapter at the
API boundary is still required (just generic instead of per-endpoint), (b)
a generic recursive snake↔camel converter loses type safety at the seam
(the slice-1 PR explicitly rejected this for that reason), (c) it adds a
build-step + tool dependency to a project that doesn't otherwise need one,
and (d) most codegen tools have their own quirks around discriminated
unions, `nullable`, and `oneOf`/`allOf` — every quirk becomes a future
debugging episode. The codegen path is worth revisiting when the API
surface grows past ~20 endpoints; for the auth + listings + search +
transactions surfaces planned today, it's overhead without payoff.

**Per-endpoint hand-rolled adapter (status quo from PR #43).** Keep the
adapter, copy the pattern into every new slice. Rejected — see the Context
section: four future slices each ship their own adapter, the duplication
compounds, and the failure mode is silent. The adapter solved slice 1; it
doesn't scale.
