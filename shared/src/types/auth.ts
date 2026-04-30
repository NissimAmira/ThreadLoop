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
}

export interface FacebookSsoCallbackInput {
  accessToken: string;
}

export type SsoCallbackInput =
  | GoogleSsoCallbackInput
  | AppleSsoCallbackInput
  | FacebookSsoCallbackInput;
