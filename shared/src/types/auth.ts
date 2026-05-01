/**
 * SSO callback request bodies. The backend dispatches by the `provider` path
 * parameter; the client sends the variant matching that provider.
 *
 * See `POST /api/auth/{provider}/callback` in `shared/openapi.yaml`.
 */

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
