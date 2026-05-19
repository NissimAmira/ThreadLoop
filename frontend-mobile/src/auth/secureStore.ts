/**
 * Thin wrapper around `expo-secure-store` for the access JWT.
 *
 * The access token lives in memory for hot paths (per RFC 0001's
 * "in-memory only" stance — same as the web client's `AuthContext`),
 * but we additionally cache it in the platform secure store so a
 * cold-start can hydrate without forcing the user to wait for the
 * `/api/auth/refresh` round-trip.
 *
 * The refresh token is NOT stored here. It rides as an httpOnly cookie
 * set by the backend; `fetch` in React Native respects the platform
 * cookie jar automatically when `credentials: "include"` is set.
 *
 * Why a wrapper:
 *   - `expo-secure-store` doesn't work on web; the wrapper short-
 *     circuits to an in-memory map there so the same code path works
 *     in `expo start --web` and in jest-expo's jsdom environment.
 *   - Jest's `jest-expo` preset auto-mocks `expo-secure-store` but
 *     the mock is per-test-isolated. Wrapping keeps the swap seam in
 *     one place if we ever need a different storage strategy (e.g.
 *     `Keychain.setGenericPassword` for shared-credential flows).
 */

import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";

const ACCESS_TOKEN_KEY = "threadloop.accessToken";

// Web / jsdom fallback. `expo-secure-store` throws on web; the in-
// memory map preserves the same get/set/clear semantics for the
// session-lifetime that matters in those environments.
const webMemoryStore = new Map<string, string>();

function isWebOrNode(): boolean {
  return Platform.OS === "web";
}

export async function setAccessToken(token: string): Promise<void> {
  if (isWebOrNode()) {
    webMemoryStore.set(ACCESS_TOKEN_KEY, token);
    return;
  }
  await SecureStore.setItemAsync(ACCESS_TOKEN_KEY, token, {
    keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
  });
}

export async function getAccessToken(): Promise<string | null> {
  if (isWebOrNode()) {
    return webMemoryStore.get(ACCESS_TOKEN_KEY) ?? null;
  }
  return SecureStore.getItemAsync(ACCESS_TOKEN_KEY);
}

export async function clearAccessToken(): Promise<void> {
  if (isWebOrNode()) {
    webMemoryStore.delete(ACCESS_TOKEN_KEY);
    return;
  }
  await SecureStore.deleteItemAsync(ACCESS_TOKEN_KEY);
}
