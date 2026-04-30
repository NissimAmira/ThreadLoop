# ADR 0002: SSO-only authentication

- **Status:** Accepted
- **Date:** 2026-04-29
- **Context links:** `docs/auth.md`, RFC 0001 (SSO authentication)

## Context

ThreadLoop needs user authentication. Conventional choice would be
email + password with optional social login. A more opinionated choice
is SSO-only — Google, Apple, and Facebook — with no password storage at
all.

Constraints in play:
- App Store Guideline 4.8 requires Sign in with Apple if any other
  social login is offered on iOS.
- Password storage is a permanent breach surface: bcrypt rounds, reset
  flows, breach notification.
- Users on a portfolio-grade marketplace expect "Sign in with Google" to
  exist; they generally don't expect or trust ad-hoc passwords.

## Decision

Authenticate exclusively via Google, Apple, or Facebook SSO. No password
fields anywhere — `users` is keyed on `(provider, provider_user_id)`. No
magic-link fallback either (would still need Apple SSO for 4.8
compliance, doubling the auth surface).

## Consequences

- (+) Zero credential storage means zero credential breach surface.
- (+) No password reset, change, or strength-validation UX to build.
- (+) Email verification comes "for free" from the providers.
- (+) Users sign in with an account they already have.
- (−) Account linking across providers is non-trivial — Apple's "Hide
  my Email" relay addresses prevent passive deduplication. Mitigated in
  RFC 0001 with an explicit linking flow.
- (−) Hard dependency on three external providers; if Apple is down,
  iOS users can't sign in. Acceptable; the alternative (offline
  fallback) is a security regression.
- (−) Cannot offer the marketplace to users without one of these
  identities. Acceptable for a consumer fashion app; would fail for
  enterprise.

## Alternatives considered

**Email + password (with optional SSO).** Conventional. Rejected — see
RFC 0001 § Alternatives for the full reasoning.

**Magic-link only.** No passwords, but bad mobile UX (app switch in the
middle of sign-in) and would still need Apple SSO for iOS 4.8
compliance.

**Outsourced auth (Auth0 / Clerk / WorkOS).** Solves the problem
end-to-end. Rejected because demonstrating real OIDC integration is
itself a portfolio signal, and free tiers have user limits.
