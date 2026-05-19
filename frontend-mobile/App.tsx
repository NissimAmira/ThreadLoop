import { StatusBar } from "expo-status-bar";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { AuthProvider } from "./src/auth/AuthContext";
import { RootNavigator } from "./src/navigation/RootNavigator";

/**
 * Root component for the Expo mobile client.
 *
 * Layering top-to-bottom:
 *   - `SafeAreaProvider` — required by `@react-navigation/native-stack`
 *     and `react-native-safe-area-context` consumers downstream.
 *   - `AuthProvider` — exposes `useAuth()` and runs the silent-refresh
 *     hydrate on first paint. Lives above the navigator so the
 *     navigator's stack switch reacts to auth-state transitions.
 *   - `RootNavigator` — switches between SignIn and Me based on auth
 *     state. See `src/navigation/RootNavigator.tsx`.
 *
 * The original health-check status pill from the v1 scaffold moved
 * into `MeScreen` (footer) rather than living at the App level — this
 * keeps the navigator stack clean while preserving the same
 * "is the backend reachable?" feedback the demo relied on.
 */
export default function App() {
  return (
    <SafeAreaProvider>
      <AuthProvider>
        <StatusBar style="auto" />
        <RootNavigator />
      </AuthProvider>
    </SafeAreaProvider>
  );
}
