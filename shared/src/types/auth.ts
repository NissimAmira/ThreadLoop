/**
 * SSO callback request bodies. The backend dispatches by the `provider` path
 * parameter; the client sends the variant matching that provider.
 *
 * See `POST /api/auth/{provider}/callback` in `shared/openapi.yaml`.
 */

import type { AuthProvider } from "./user";

export interface GoogleSsoCallbackInput {
  idToken: string;
}

export interface AppleSsoCallbackInput {
  idToken: string;
  code: string;
  /**
   * Apple surfaces the user's name only on the first sign-in (and only when
   * the app requested the `name` scope). Subsequent sign-ins omit it; the
   * backend reuses the existing `users.display_name` in that case.
   */
  name?: string;
}

export interface FacebookSsoCallbackInput {
  accessToken: string;
}

export type SsoCallbackInput =
  | GoogleSsoCallbackInput
  | AppleSsoCallbackInput
  | FacebookSsoCallbackInput;

/**
 * Body for `POST /api/auth/link` — resolves a pending account-link by
 * re-authenticating with the original provider.
 *
 * The flow:
 *   1. User signs in with provider B; the callback returns a `Session` with
 *      `linkRequired: true`, `linkProvider: A` (the existing account's
 *      provider), and a short-lived `linkToken`.
 *   2. Client prompts the user to re-authenticate with provider A and
 *      collects a fresh A-credential (same shape A's callback would accept).
 *   3. Client posts `{ linkToken, originalProvider: A, credential }` to
 *      `POST /api/auth/link`. Backend validates the `linkToken`, re-verifies
 *      the credential via A's verifier, asserts the resulting identity
 *      matches the existing user's primary `(provider, providerUserId)`,
 *      adds the second-provider identity to that user's `user_identities`
 *      rows, and mints a fresh session for the merged account.
 *
 * `linkToken` is single-use (server-side `jti` record). Replays return 401.
 * Tokens older than the link-token TTL (default 10 minutes) return 401.
 */
export interface LinkRequestInput {
  linkToken: string;
  originalProvider: AuthProvider;
  /**
   * Provider-specific credential matching `originalProvider`. Same shape
   * the original-provider callback would accept.
   */
  credential:
    | GoogleSsoCallbackInput
    | AppleSsoCallbackInput
    | FacebookSsoCallbackInput;
}
