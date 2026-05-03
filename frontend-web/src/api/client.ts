import type { HealthResponse, Session, User } from "@threadloop/shared";

const API_BASE = import.meta.env.VITE_API_URL ?? "";

/**
 * Thrown for any non-2xx HTTP response from the API. The `code` field, when
 * present, is the stable machine-readable identifier from the OpenAPI `Error`
 * envelope — callers branch on it (e.g. `link_required`, `invalid_token`).
 */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
    public requestId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface RequestOptions {
  method?: "GET" | "POST" | "DELETE";
  body?: unknown;
  accessToken?: string;
  /** When false, do not attach JSON Content-Type (e.g. empty POST). */
  json?: boolean;
}

async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, accessToken, json = true } = opts;
  const headers: Record<string, string> = {};
  if (json && body !== undefined) headers["Content-Type"] = "application/json";
  if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;

  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "include",
  });

  if (res.status === 204) return undefined as T;

  let payload: unknown;
  try {
    payload = await res.json();
  } catch {
    payload = null;
  }

  if (!res.ok) {
    const err = (payload ?? {}) as { code?: string; message?: string; requestId?: string };
    throw new ApiError(
      res.status,
      err.message ?? `Request failed: ${res.status}`,
      err.code,
      err.requestId,
    );
  }

  return payload as T;
}

// Wire shape is camelCase on every property per ADR 0009 — see
// `docs/adrs/0009-camelcase-on-the-wire.md`. The previous per-endpoint
// snake→camel adapter (PR #43) was retired in #44; methods now return the
// shared TS types directly with no boundary translation.

export const api = {
  health: () => request<HealthResponse>("/api/health"),

  auth: {
    /** POST /api/auth/google/callback — exchange a Google ID token for a session. */
    googleCallback: (idToken: string): Promise<Session> =>
      request<Session>("/api/auth/google/callback", {
        method: "POST",
        body: { idToken },
      }),

    /** POST /api/auth/refresh — rotate the refresh cookie for a new access token. */
    refresh: (): Promise<Session> =>
      request<Session>("/api/auth/refresh", { method: "POST" }),

    /** POST /api/auth/logout — revoke + clear the refresh cookie. Idempotent. */
    logout: () => request<void>("/api/auth/logout", { method: "POST" }),
  },

  /** GET /api/me — resolve the bearer access token to the current user. */
  me: (accessToken: string): Promise<User> => request<User>("/api/me", { accessToken }),
};
