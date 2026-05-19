// Jest setup for the Expo mobile client.
//
// `jest-expo` ships an auto-mock for `expo-secure-store` that returns
// undefined for every call. That's fine for most tests, but the auth
// context needs a working in-memory simulation so the silent-refresh
// path can be exercised end-to-end. The wrapper at
// `src/auth/secureStore.ts` already falls back to an in-memory map on
// `Platform.OS === "web"`; jest-expo defaults `Platform.OS` to "ios",
// so we patch it here to "web" for the jest run.

const { Platform } = require("react-native");
Object.defineProperty(Platform, "OS", { get: () => "web" });
