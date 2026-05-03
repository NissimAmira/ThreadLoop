import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { AuthenticatedSession, User } from "@threadloop/shared";
import { ApiError, api } from "../api/client";

/**
 * Auth state for the web client.
 *
 * Three states only — kept deliberately small so consumers don't write
 * boolean ladders to figure out where they are:
 *
 *   - `loading`       — first-paint silent refresh hasn't resolved yet.
 *   - `anonymous`     — no session; show sign-in.
 *   - `authenticated` — `user` and `accessToken` are both present.
 *
 * The access token lives in memory only (per `docs/auth.md` § Flow). The
 * refresh token rides in an httpOnly cookie that this code never touches.
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
      try {
        const session = await api.auth.refresh();
        if (cancelled) return;
        if (session.linkRequired) {
          setState({ status: "anonymous" });
          return;
        }
        setState({
          status: "authenticated",
          user: session.user,
          accessToken: session.accessToken,
        });
      } catch (err) {
        if (cancelled) return;
        if (!(err instanceof ApiError)) {
          // Network failure during hydration: degrade to anonymous rather
          // than spinning forever. The user can still hit /sign-in.
          setState({ status: "anonymous" });
          return;
        }
        setState({ status: "anonymous" });
      }
    };
    hydrate();
    return () => {
      cancelled = true;
    };
  }, []);

  const signIn = useCallback((session: AuthenticatedSession) => {
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
      // Logout is idempotent server-side; even if the call fails (network,
      // cookie already cleared) we still want to drop the in-memory state.
    }
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
