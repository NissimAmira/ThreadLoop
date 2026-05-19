import * as Google from "expo-auth-session/providers/google";
import { Platform } from "react-native";
import { config } from "../config/env";

/**
 * Hook wrapping `expo-auth-session`'s Google provider.
 *
 * The hook returns a tuple `[request, response, promptAsync]` matching
 * `expo-auth-session`'s convention. The caller decides when to fire
 * `promptAsync()` (on button press) and which response state to react
 * to. We expose a typed convenience selector for the ID token rather
 * than re-implementing the auth-session machinery.
 *
 * The OpenID `id_token` scope is requested explicitly so the backend's
 * `POST /api/auth/google/callback` receives the same ID-token shape the
 * web client posts (Google's JWKS verifier on the BE expects the OIDC
 * id_token, not an OAuth access token).
 *
 * Per-platform client IDs (the Cloud Console issues distinct OAuth
 * credentials per platform — iOS / Android / Web). `expo-auth-session`
 * picks the right one based on the runtime platform.
 */

export interface GoogleSignInResult {
  idToken: string;
}

export function useGoogleAuth() {
  const [request, response, promptAsync] = Google.useIdTokenAuthRequest({
    iosClientId: config.googleClientIdIos,
    androidClientId: config.googleClientIdAndroid,
    // expo-auth-session also accepts `webClientId`; we don't pass it
    // because the mobile build doesn't run in a web context — the
    // web client has its own `frontend-web/src/auth/google.ts`.
  });

  return { request, response, promptAsync };
}

/**
 * Extracts the ID token from a Google auth-session response.
 * Returns null when the response isn't a successful id_token grant
 * (user cancelled, dismissed the popup, or hit a Google-side error).
 */
export function extractGoogleIdToken(
  response: Awaited<ReturnType<typeof Google.useIdTokenAuthRequest>>[1],
): string | null {
  if (!response) return null;
  if (response.type !== "success") return null;
  // `useIdTokenAuthRequest` resolves the id_token into `params.id_token`.
  const idToken = response.params?.id_token;
  return typeof idToken === "string" ? idToken : null;
}

/** Quick guard for unconfigured client IDs — surfaces a dev-loud error
 *  when a button is clicked on a build with no client ID set for the
 *  current platform. */
export function isGoogleConfigured(): boolean {
  if (Platform.OS === "ios") return Boolean(config.googleClientIdIos);
  if (Platform.OS === "android") return Boolean(config.googleClientIdAndroid);
  // expo start --web works through `expo-auth-session`'s web flow but
  // the dedicated `frontend-web` client is canonical there; treat as
  // unconfigured to nudge contributors to use the web app instead.
  return false;
}
