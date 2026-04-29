import { useEffect, useState } from "react";
import type { DependencyStatus, HealthResponse } from "@threadloop/shared";
import { api } from "../api/client";

const POLL_MS = 30_000;

const dotColor: Record<DependencyStatus | "unknown", string> = {
  ok: "bg-emerald-500",
  degraded: "bg-amber-500",
  down: "bg-rose-500",
  unknown: "bg-neutral-400",
};

const labelFor = (s: DependencyStatus | "unknown") =>
  s === "ok" ? "All systems operational" :
  s === "degraded" ? "Degraded performance" :
  s === "down" ? "Service unavailable" :
  "Checking…";

export function StatusBar() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const overall: DependencyStatus | "unknown" = health?.status ?? "unknown";

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await api.health();
        if (!cancelled) setHealth(r);
      } catch {
        if (!cancelled) setHealth({ status: "down", version: "?", db: "down", redis: "down", meili: "down" });
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    tick();
    const id = setInterval(tick, POLL_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  return (
    <footer
      className="border-t bg-white"
      data-testid="status-bar"
      data-status={overall}
    >
      <div className="max-w-5xl mx-auto px-6 py-3 flex items-center gap-3 text-sm">
        <span className={`inline-block h-2.5 w-2.5 rounded-full ${dotColor[overall]}`} aria-hidden />
        <span className="font-medium">{labelFor(overall)}</span>
        {health && !loading && (
          <span className="text-neutral-500">
            · API v{health.version} · db {health.db} · redis {health.redis} · meili {health.meili}
          </span>
        )}
      </div>
    </footer>
  );
}
