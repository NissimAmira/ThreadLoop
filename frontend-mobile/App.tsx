import { useEffect, useState } from "react";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";
import { StatusBar } from "expo-status-bar";
import type { DependencyStatus, HealthResponse } from "@threadloop/shared";

const API_URL = process.env.EXPO_PUBLIC_API_URL ?? "http://localhost:8000";

const dotColor: Record<DependencyStatus | "unknown", string> = {
  ok: "#10b981",
  degraded: "#f59e0b",
  down: "#ef4444",
  unknown: "#9ca3af",
};

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await fetch(`${API_URL}/api/health`);
        const body = (await r.json()) as HealthResponse;
        if (!cancelled) setHealth(body);
      } catch {
        if (!cancelled) setHealth({ status: "down", version: "?", db: "down", redis: "down", meili: "down" });
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    tick();
    const id = setInterval(tick, 30_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const overall: DependencyStatus | "unknown" = health?.status ?? "unknown";

  return (
    <View style={styles.root}>
      <StatusBar style="auto" />
      <View style={styles.hero}>
        <Text style={styles.brand}>
          <Text style={styles.brandHighlight}>Thread</Text>Loop
        </Text>
        <Text style={styles.tagline}>Buy, sell, swap — second-hand fashion with AR try-on.</Text>
      </View>

      <View style={styles.statusBar}>
        {loading ? (
          <ActivityIndicator />
        ) : (
          <>
            <View style={[styles.dot, { backgroundColor: dotColor[overall] }]} />
            <Text style={styles.statusText}>
              {overall === "ok" ? "All systems operational" :
               overall === "degraded" ? "Degraded performance" :
               overall === "down" ? "Service unavailable" :
               "Checking…"}
            </Text>
          </>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#fafafa", justifyContent: "space-between" },
  hero: { padding: 32, paddingTop: 80 },
  brand: { fontSize: 32, fontWeight: "700", color: "#111827" },
  brandHighlight: { color: "#5b3df6" },
  tagline: { marginTop: 8, fontSize: 16, color: "#4b5563" },
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
});
