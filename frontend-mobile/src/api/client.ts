import type {
  FacebookSsoCallbackInput,
  GoogleSsoCallbackInput,
  HealthResponse,
  LinkRequestInput,
  Session,
  User,
} from "@threadloop/shared";
import { config } from "../config/env";

/**
 * HTTP client mirroring `frontend-web/src/api/client.ts`. Same camelCase
 * wire shape (ADR 0009); consumes the typed shapes from `@threadloop/shared`
 * directly with no boundary translation.
 *
 * Cookies are sent with `credentials: "include"`. React Native's `fetch`
 * implementation persists cookies in the platform cookie jar (iOS:
 * NSURLCredentialStorage, Android: WebKit cookie store) so the
 * httpOnly refresh cookie survives across cold starts naturally — no
 * extra wiring needed.
 */

export class ApiError extends Error {
  status: number;
  code?: string;
  requestId?: string;

  constructor(status: number, message: string, code?: string, requestId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
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

  const res = await fetch(`${config.apiBaseUrl}${path}`, {
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

export const api = {
  health: () => request<HealthResponse>("/api/health"),

  auth: {
    /** POST /api/auth/google/callback — exchange a Google ID token for a session. */
    googleCallback: (input: GoogleSsoCallbackInput): Promise<Session> =>
      request<Session>("/api/auth/google/callback", {
        method: "POST",
        body: input,
      }),

    /**
     * POST /api/auth/facebook/callback — exchange a Facebook user access
     * token for a session. The backend re-validates against Graph API
     * (`/debug_token` then `/me`); the client just posts the access token
     * surfaced by `expo-auth-session`.
     */
    facebookCallback: (input: FacebookSsoCallbackInput): Promise<Session> =>
      request<Session>("/api/auth/facebook/callback", {
        method: "POST",
        body: input,
      }),

    /**
     * POST /api/auth/link — resolve a pending account-link by re-
     * authenticating with the original provider. Consumes the short-
     * lived `linkToken` issued by a callback's `link_required`
     * envelope. On success the second-provider identity is merged onto
     * the existing user and the response is a standard `Session`
     * envelope with `linkRequired: false`. Mirrors the web client.
     */
    link: (input: LinkRequestInput): Promise<Session> =>
      request<Session>("/api/auth/link", {
        method: "POST",
        body: input,
      }),

    /** POST /api/auth/refresh — rotate the refresh cookie for a new access token. */
    refresh: (): Promise<Session> =>
      request<Session>("/api/auth/refresh", { method: "POST" }),

    /** POST /api/auth/logout — revoke + clear the refresh cookie. Idempotent. */
    logout: () => request<void>("/api/auth/logout", { method: "POST" }),
  },

  /** GET /api/me — resolve the bearer access token to the current user. */
  me: (accessToken: string): Promise<User> =>
    request<User>("/api/me", { accessToken }),
};
