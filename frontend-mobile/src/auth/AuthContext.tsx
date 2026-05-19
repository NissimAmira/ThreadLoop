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
 *
 * `offline` is a sibling flag (not a state) — it's only ever true when
 * we degraded onto the cached-token + `/api/me` path because the
 * `/api/auth/refresh` round-trip threw a network failure (or 5xx).
 * Screens (e.g. MeScreen) surface a "Working offline" banner when set.
 * The next successful refresh clears it.
 */
export type AuthState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "authenticated"; user: User; accessToken: string };

export interface AuthContextValue {
  state: AuthState;
  /** True when we hydrated via the cached-token degraded path. */
  offline: boolean;
  /** Promote a fresh callback Session into the active in-memory state. */
  signIn: (session: AuthenticatedSession) => void;
  /** Revoke the refresh cookie server-side and drop the in-memory state. */
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });
  const [offline, setOffline] = useState(false);

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
          setOffline(false);
          setState({ status: "anonymous" });
          return;
        }
        await setAccessToken(session.accessToken);
        setOffline(false);
        setState({
          status: "authenticated",
          user: session.user,
          accessToken: session.accessToken,
        });
      } catch (err) {
        if (cancelled) return;
        // Only a genuine 401 from `/api/auth/refresh` means "no valid
        // refresh cookie" — anything else (5xx, 429, network reject)
        // is a transport / availability problem and should fall
        // through to the degraded cached-token path rather than
        // signing the user out. Mirrors the documented intent in
        // `docs/auth.md` § "Network-failure fallback".
        if (err instanceof ApiError && err.status === 401) {
          // Drop any stale stored access token so a later sign-in
          // starts fresh.
          await clearAccessToken();
          setOffline(false);
          setState({ status: "anonymous" });
          return;
        }
        // Transport / availability failure during hydration: try the
        // stored access token + /api/me as a degraded path so
        // offline-but-recently-signed-in users see their last-known
        // profile rather than a sign-in screen. This is safe because
        // the fallback still round-trips to the backend — a
        // server-revoked or expired token will 401 from /api/me and
        // we'll clear the cache and fall back to anonymous; a
        // truly-offline /api/me also throws and clears. So a cached
        // token never grants offline access to a logged-out account.
        try {
          const stored = await getAccessToken();
          if (!stored) {
            setOffline(false);
            setState({ status: "anonymous" });
            return;
          }
          const user = await api.me(stored);
          if (cancelled) return;
          setOffline(true);
          setState({
            status: "authenticated",
            user,
            accessToken: stored,
          });
        } catch {
          if (cancelled) return;
          await clearAccessToken();
          setOffline(false);
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
    // A fresh callback session is by definition online; clear any
    // lingering offline flag from a prior degraded hydrate.
    setOffline(false);
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
    setOffline(false);
    setState({ status: "anonymous" });
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ state, offline, signIn, signOut }),
    [state, offline, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
