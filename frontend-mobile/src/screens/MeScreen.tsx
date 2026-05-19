import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import type { DependencyStatus, HealthResponse } from "@threadloop/shared";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";

/**
 * MeScreen — mobile equivalent of `frontend-web/src/pages/MePage.tsx`.
 *
 * Renders the signed-in user's display name, email, and provider,
 * plus a sign-out button. Footer carries the same health-check status
 * pill the original `App.tsx` rendered so the demo flow still surfaces
 * "backend reachable / not reachable" without a separate screen.
 */

const dotColor: Record<DependencyStatus | "unknown", string> = {
  ok: "#10b981",
  degraded: "#f59e0b",
  down: "#ef4444",
  unknown: "#9ca3af",
};

export function MeScreen() {
  const { state, offline, signOut } = useAuth();
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const body = await api.health();
        if (!cancelled) setHealth(body);
      } catch {
        if (!cancelled) {
          setHealth({
            status: "down",
            version: "?",
            db: "down",
            redis: "down",
            meili: "down",
          });
        }
      } finally {
        if (!cancelled) setHealthLoading(false);
      }
    };
    void tick();
    const id = setInterval(tick, 30_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (state.status !== "authenticated") {
    // RootNavigator switches the stack on `state.status` so we should
    // never actually paint MeScreen in any other state. Defensive
    // fallback for the unmount-race / RN remount edge case.
    return (
      <View style={styles.root}>
        <ActivityIndicator />
      </View>
    );
  }

  const overall: DependencyStatus | "unknown" = health?.status ?? "unknown";
  const { user } = state;

  return (
    <View style={styles.root}>
      {offline && (
        <View
          accessibilityRole="alert"
          accessibilityLabel="Working offline — some features may be unavailable"
          style={styles.offlineBanner}
          testID="me-offline-banner"
        >
          <Text style={styles.offlineBannerText}>
            Working offline — some features may be unavailable
          </Text>
        </View>
      )}
      <ScrollView contentContainerStyle={styles.scroll}>
        <View style={styles.card}>
          <Text accessibilityRole="header" style={styles.title}>
            Your profile
          </Text>

          <View style={styles.field}>
            <Text style={styles.label}>Display name</Text>
            <Text style={styles.value} testID="me-display-name">
              {user.displayName}
            </Text>
          </View>

          <View style={styles.field}>
            <Text style={styles.label}>Email</Text>
            <Text style={styles.value} testID="me-email">
              {user.email ?? "Not provided"}
            </Text>
          </View>

          <View style={styles.field}>
            <Text style={styles.label}>Provider</Text>
            <Text style={styles.value} testID="me-provider">
              {user.provider}
            </Text>
          </View>

          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Sign out"
            onPress={() => {
              void signOut();
            }}
            style={({ pressed }) => [
              styles.signOutButton,
              pressed && styles.buttonPressed,
            ]}
            testID="me-sign-out"
          >
            <Text style={styles.signOutText}>Sign out</Text>
          </Pressable>
        </View>
      </ScrollView>

      <View style={styles.statusBar}>
        {healthLoading ? (
          <ActivityIndicator />
        ) : (
          <>
            <View style={[styles.dot, { backgroundColor: dotColor[overall] }]} />
            <Text style={styles.statusText}>
              {overall === "ok"
                ? "All systems operational"
                : overall === "degraded"
                  ? "Degraded performance"
                  : overall === "down"
                    ? "Service unavailable"
                    : "Checking…"}
            </Text>
          </>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#fafafa" },
  scroll: { padding: 24 },
  card: {
    backgroundColor: "#ffffff",
    borderRadius: 16,
    padding: 24,
    gap: 16,
    shadowColor: "#000",
    shadowOpacity: 0.05,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  title: {
    fontSize: 22,
    fontWeight: "700",
    color: "#111827",
    marginBottom: 8,
  },
  field: { gap: 4 },
  label: {
    fontSize: 11,
    color: "#6b7280",
    fontWeight: "700",
    letterSpacing: 0.5,
    textTransform: "uppercase",
  },
  value: { fontSize: 16, color: "#111827" },
  signOutButton: {
    marginTop: 12,
    minHeight: 48,
    borderRadius: 8,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderColor: "#d1d5db",
    backgroundColor: "#ffffff",
  },
  buttonPressed: { opacity: 0.85 },
  signOutText: { color: "#1f2937", fontWeight: "600", fontSize: 16 },
  statusBar: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: 24,
    paddingVertical: 16,
    borderTopWidth: 1,
    borderTopColor: "#e5e7eb",
    backgroundColor: "#ffffff",
  },
  dot: { width: 10, height: 10, borderRadius: 5 },
  statusText: { fontSize: 14, color: "#374151" },
  offlineBanner: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    backgroundColor: "#fef3c7",
    borderBottomWidth: 1,
    borderBottomColor: "#fde68a",
  },
  offlineBannerText: {
    fontSize: 13,
    color: "#92400e",
    fontWeight: "500",
    textAlign: "center",
  },
});
