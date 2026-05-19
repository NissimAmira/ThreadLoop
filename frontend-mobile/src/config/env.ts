/**
 * Runtime config sourced from `process.env.EXPO_PUBLIC_*`.
 *
 * Expo inlines every `EXPO_PUBLIC_*` env var into the JS bundle at build
 * time — these are the *only* env vars the mobile client can read. Per
 * the strict `=== "true"` parse rule (mirrored from the web client),
 * any value other than the literal string `"true"` (including unset)
 * resolves to `false`. This keeps the FE/BE flag wiring symmetric: a
 * stale `EXPO_PUBLIC_APPLE_CLIENT_ID` in a local `.env` can't accidentally
 * re-enable a button the build isn't meant to ship.
 */

const isTrue = (value: string | undefined): boolean => value === "true";

export interface MobileConfig {
  /** Base URL of the FastAPI backend. */
  apiBaseUrl: string;
  /** Provider gating flags. Mirror the BE `<PROVIDER>_ENABLED` flags. */
  googleEnabled: boolean;
  facebookEnabled: boolean;
  /**
   * Apple is descoped from Epic #11 (see `docs/rfcs/0001-auth-sso.md`
   * § "Deferred providers"). The flag exists so the surface stays
   * symmetric with the web client; flipping it alone won't light up
   * a button — `expo-apple-authentication` is not bundled.
   */
  appleEnabled: boolean;
  /** Per-platform Google OAuth client IDs (Cloud Console → OAuth 2.0). */
  googleClientIdIos: string | undefined;
  googleClientIdAndroid: string | undefined;
  /** Meta App ID (same as backend `FACEBOOK_APP_ID`). */
  facebookAppId: string | undefined;
}

export function readConfig(): MobileConfig {
  return {
    apiBaseUrl:
      process.env.EXPO_PUBLIC_API_BASE_URL ??
      process.env.EXPO_PUBLIC_API_URL ??
      "http://localhost:8000",
    googleEnabled: isTrue(process.env.EXPO_PUBLIC_GOOGLE_ENABLED),
    facebookEnabled: isTrue(process.env.EXPO_PUBLIC_FACEBOOK_ENABLED),
    appleEnabled: isTrue(process.env.EXPO_PUBLIC_APPLE_ENABLED),
    googleClientIdIos: process.env.EXPO_PUBLIC_GOOGLE_CLIENT_ID_IOS || undefined,
    googleClientIdAndroid:
      process.env.EXPO_PUBLIC_GOOGLE_CLIENT_ID_ANDROID || undefined,
    facebookAppId: process.env.EXPO_PUBLIC_FACEBOOK_APP_ID || undefined,
  };
}

export const config: MobileConfig = readConfig();
