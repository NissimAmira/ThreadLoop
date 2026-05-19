import type {
  AppleSsoCallbackInput,
  FacebookSsoCallbackInput,
  HealthResponse,
  LinkRequestInput,
  Session,
  User,
} from "@threadloop/shared";

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

    /**
     * POST /api/auth/apple/callback — exchange an Apple ID token + code for a session.
     *
     * Apple's contract requires both `idToken` and `code`; `name` is optional
     * and only sent on the user's very first sign-in (Apple omits it on every
     * subsequent flow, and the backend reuses the existing `display_name`).
     */
    appleCallback: (input: AppleSsoCallbackInput): Promise<Session> =>
      request<Session>("/api/auth/apple/callback", {
        method: "POST",
        body: input,
      }),

    /**
     * POST /api/auth/facebook/callback — exchange a Facebook user access
     * token for a session. The backend re-validates against Graph API
     * (`/debug_token` then `/me`); this client posts the user access token
     * surfaced by `FB.login()` and trusts the BE to do the heavy lifting.
     */
    facebookCallback: (input: FacebookSsoCallbackInput): Promise<Session> =>
      request<Session>("/api/auth/facebook/callback", {
        method: "POST",
        body: input,
      }),

    /**
     * POST /api/auth/link — resolve a pending account-link by re-authenticating
     * with the original provider. Consumes the short-lived `linkToken` issued
     * by a callback's `link_required` envelope. On success the second-provider
     * identity is merged onto the existing user's `user_identities` rows and
     * the response is the standard `Session` envelope (with `linkRequired:
     * false`).
     *
     * The BE collapses every link-token-or-credential failure into a single
     * 401 envelope (per PR #64's "Failure mapping"); 409 is reserved for the
     * second-provider identity already being claimed by a different user.
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
  me: (accessToken: string): Promise<User> => request<User>("/api/me", { accessToken }),
};
