import type {
  AuthenticatedSession,
  HealthResponse,
  PendingLinkSession,
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
    const err = (payload ?? {}) as { code?: string; message?: string; request_id?: string };
    throw new ApiError(
      res.status,
      err.message ?? `Request failed: ${res.status}`,
      err.code,
      err.request_id,
    );
  }

  return payload as T;
}

// --- Wire-shape adapters ----------------------------------------------------
//
// The backend serializes per OpenAPI in snake_case (e.g. `access_token`,
// `display_name`); the shared TS types are camelCase. We convert at the API
// boundary so the rest of the web workspace consumes the typed shapes from
// `@threadloop/shared` directly. Keep these adapters narrow and explicit —
// each new endpoint adds its own mapping rather than reaching for a generic
// recursive snake↔camel converter, which would lose type safety.

interface UserWire {
  id: string;
  provider: User["provider"];
  email: string | null;
  email_verified: boolean;
  display_name: string;
  avatar_url: string | null;
  can_sell: boolean;
  can_purchase: boolean;
  seller_rating: number | null;
  created_at: string;
  updated_at: string;
}

interface SessionWire {
  link_required: boolean;
  access_token?: string;
  expires_at?: string;
  user?: UserWire;
  link_provider?: User["provider"];
  link_token?: string;
}

function userFromWire(w: UserWire): User {
  return {
    id: w.id,
    provider: w.provider,
    email: w.email,
    emailVerified: w.email_verified,
    displayName: w.display_name,
    avatarUrl: w.avatar_url,
    canSell: w.can_sell,
    canPurchase: w.can_purchase,
    sellerRating: w.seller_rating,
    createdAt: w.created_at,
    updatedAt: w.updated_at,
  };
}

function sessionFromWire(w: SessionWire): Session {
  if (w.link_required) {
    if (!w.link_provider || !w.link_token) {
      throw new ApiError(500, "Malformed link_required response", "malformed_response");
    }
    const pending: PendingLinkSession = {
      linkRequired: true,
      linkProvider: w.link_provider,
      linkToken: w.link_token,
    };
    return pending;
  }
  if (!w.access_token || !w.expires_at || !w.user) {
    throw new ApiError(500, "Malformed session response", "malformed_response");
  }
  const ok: AuthenticatedSession = {
    linkRequired: false,
    accessToken: w.access_token,
    expiresAt: w.expires_at,
    user: userFromWire(w.user),
  };
  return ok;
}

export const api = {
  health: () => request<HealthResponse>("/api/health"),

  auth: {
    /** POST /api/auth/google/callback — exchange a Google ID token for a session. */
    googleCallback: async (idToken: string): Promise<Session> => {
      const wire = await request<SessionWire>("/api/auth/google/callback", {
        method: "POST",
        body: { id_token: idToken },
      });
      return sessionFromWire(wire);
    },

    /** POST /api/auth/refresh — rotate the refresh cookie for a new access token. */
    refresh: async (): Promise<Session> => {
      const wire = await request<SessionWire>("/api/auth/refresh", { method: "POST" });
      return sessionFromWire(wire);
    },

    /** POST /api/auth/logout — revoke + clear the refresh cookie. Idempotent. */
    logout: () => request<void>("/api/auth/logout", { method: "POST" }),
  },

  /** GET /api/me — resolve the bearer access token to the current user. */
  me: async (accessToken: string): Promise<User> => {
    const wire = await request<UserWire>("/api/me", { accessToken });
    return userFromWire(wire);
  },
};
