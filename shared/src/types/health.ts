export type DependencyStatus = "ok" | "degraded" | "down";

export interface HealthResponse {
  status: DependencyStatus;
  version: string;
  db: DependencyStatus;
  redis: DependencyStatus;
  meili: DependencyStatus;
}
