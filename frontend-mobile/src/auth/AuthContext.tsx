import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { ReactNode } from "react";
import type { AuthenticatedSession, User } from "@threadloop/shared";
import { ApiError, api } from "../api/client";
import {
  clearAccessToken,
  getAccessToken,
  setAccessToken,
} from "./secureStore";

/**
 * Auth state for the mobile client.
 *
 * Three states only — kept deliberately small so consumers don't write
 * boolean ladders to figure out where they are. Mirrors the shape used
 * by `frontend-web/src/auth/AuthContext.tsx` so the two clients share
 * mental model and the same `useAuth()` ergonomics.
 *
 *   - `loading`       — first-paint silent refresh hasn't resolved yet.
 *   - `anonymous`     — no session; show sign-in.
 *   - `authenticated` — `user` and `accessToken` are both present.
 *
 * The access token lives in memory for hot paths AND is mirrored to
 * `expo-secure-store` so a cold-start can hydrate the user view
 * immediately while the silent-refresh round-trip is in flight. The
 * refresh token is the httpOnly cookie set by the backend; React
 * Native's `fetch` cookie jar handles it transparently with
 * `credentials: "include"`.
 */
export type AuthState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "authenticated"; user: User; accessToken: string };

export interface AuthContextValue {
  state: AuthState;
  /** Promote a fresh callback Session into the active in-memory state. */
  signIn: (session: AuthenticatedSession) => void;
  /** Revoke the refresh cookie server-side and drop the in-memory state. */
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;

    const hydrate = async () => {
      // Step 1: try refreshing the session via the httpOnly cookie.
      // This is the canonical "am I signed in?" check — the server
      // owns the source of truth and the cookie survives across cold
      // starts.
      try {
        const session = await api.auth.refresh();
        if (cancelled) return;
        if (session.linkRequired) {
          // The refresh route should never return a link-required
          // envelope; treat as anonymous defensively.
          await clearAccessToken();
          setState({ status: "anonymous" });
          return;
        }
        await setAccessToken(session.accessToken);
        setState({
          status: "authenticated",
          user: session.user,
          accessToken: session.accessToken,
        });
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError) {
          // 401 = no valid refresh cookie. Drop any stale stored
          // access token so a later sign-in starts fresh.
          await clearAccessToken();
          setState({ status: "anonymous" });
          return;
        }
        // Network failure during hydration: try the stored access
        // token + /api/me as a degraded path so offline-but-recently-
        // signed-in users see their last-known profile rather than a
        // sign-in screen. If the stored token has expired the BE
        // will 401 and we fall back to anonymous.
        try {
          const stored = await getAccessToken();
          if (!stored) {
            setState({ status: "anonymous" });
            return;
          }
          const user = await api.me(stored);
          if (cancelled) return;
          setState({
            status: "authenticated",
            user,
            accessToken: stored,
          });
        } catch {
          if (cancelled) return;
          await clearAccessToken();
          setState({ status: "anonymous" });
        }
      }
    };

    void hydrate();
    return () => {
      cancelled = true;
    };
  }, []);

  const signIn = useCallback((session: AuthenticatedSession) => {
    void setAccessToken(session.accessToken);
    setState({
      status: "authenticated",
      user: session.user,
      accessToken: session.accessToken,
    });
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.auth.logout();
    } catch {
      // Logout is idempotent server-side; even if the call fails
      // (network, cookie already cleared) we still want to drop the
      // in-memory and on-device state.
    }
    await clearAccessToken();
    setState({ status: "anonymous" });
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ state, signIn, signOut }),
    [state, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
