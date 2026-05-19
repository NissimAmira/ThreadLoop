import * as Facebook from "expo-auth-session/providers/facebook";
import { config } from "../config/env";

/**
 * Hook wrapping `expo-auth-session`'s Facebook provider.
 *
 * Facebook's mobile flow returns an OAuth access token (no OIDC
 * id_token surface — Facebook is OAuth-only, not OpenID Connect).
 * The backend's `POST /api/auth/facebook/callback` re-validates that
 * token via Graph API `/debug_token` + `/me`, so all the mobile
 * client has to do is surface the access token verbatim.
 *
 * The `email` permission is requested but optional: the user can
 * decline it in Facebook's consent dialog, in which case the BE
 * creates a row with `email=null` and the FE just lets sign-in
 * complete — mirrors the web client's posture (`docs/auth.md`
 * § "Web client / Facebook Login SDK").
 */

export interface FacebookSignInResult {
  accessToken: string;
}

export function useFacebookAuth() {
  const [request, response, promptAsync] = Facebook.useAuthRequest({
    clientId: config.facebookAppId ?? "",
    // `email` is the Facebook permission that grants access to the
    // user's primary email; `public_profile` is granted by default.
    scopes: ["public_profile", "email"],
  });

  return { request, response, promptAsync };
}

/**
 * Extracts the access token from a Facebook auth-session response.
 * Returns null when the response isn't a successful grant (user
 * dismissed the consent dialog, denied the app, etc.).
 */
export function extractFacebookAccessToken(
  response: Awaited<ReturnType<typeof Facebook.useAuthRequest>>[1],
): string | null {
  if (!response) return null;
  if (response.type !== "success") return null;
  const accessToken = response.authentication?.accessToken;
  return typeof accessToken === "string" ? accessToken : null;
}

export function isFacebookConfigured(): boolean {
  return Boolean(config.facebookAppId);
}
