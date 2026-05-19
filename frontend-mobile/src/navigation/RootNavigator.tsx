import { NavigationContainer } from "@react-navigation/native";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";
import { useAuth } from "../auth/AuthContext";
import { MeScreen } from "../screens/MeScreen";
import { SignInScreen } from "../screens/SignInScreen";

/**
 * Root navigator. Switches the stack based on `useAuth().state.status`:
 *
 *   - `loading`       → splash with `<ActivityIndicator />`.
 *   - `anonymous`     → SignInScreen.
 *   - `authenticated` → MeScreen.
 *
 * Mirrors the web client's pattern (auth-gated routes). Using
 * conditional stacks here (rather than per-screen `useEffect` redirects)
 * means a sign-out can't accidentally render a protected screen for one
 * frame — the stack unmounts entirely.
 */

const Stack = createNativeStackNavigator();

export function RootNavigator() {
  const { state } = useAuth();

  if (state.status === "loading") {
    // Cold-start splash. Visible label + screen-reader label both carry
    // "Signing you in…" so sighted users get context during a slow
    // refresh (3G / cold backend) and VoiceOver / TalkBack announces
    // the same. Mirrors web's brief "checking session" state.
    return (
      <View
        style={styles.loading}
        accessible
        accessibilityLabel="Signing you in"
      >
        <ActivityIndicator />
        <Text style={styles.loadingText}>Signing you in…</Text>
      </View>
    );
  }

  return (
    <NavigationContainer>
      <Stack.Navigator>
        {state.status === "authenticated" ? (
          <Stack.Screen
            name="Me"
            component={MeScreen}
            options={{ title: "ThreadLoop" }}
          />
        ) : (
          <Stack.Screen
            name="SignIn"
            component={SignInScreen}
            options={{ title: "Sign in" }}
          />
        )}
      </Stack.Navigator>
    </NavigationContainer>
  );
}

const styles = StyleSheet.create({
  loading: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#fafafa",
    gap: 12,
  },
  loadingText: {
    fontSize: 14,
    color: "#374151",
  },
});
