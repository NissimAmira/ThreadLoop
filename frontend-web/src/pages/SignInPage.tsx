import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ApiError, api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { loadGoogleIdentity } from "../auth/google";
import type { GoogleCredentialResponse } from "../auth/google";

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID ?? "";
const LINK_REQUIRED_MESSAGE =
  "This email is registered with another provider; please sign in with that provider instead.";

type Status = "idle" | "loading-sdk" | "ready" | "exchanging" | "error";

/**
 * Constrain `?next=` to same-origin app paths. Anything else (protocol-relative
 * `//evil.example.com`, absolute `http://evil`, `javascript:` URIs) collapses
 * to `/` so a crafted sign-in link can't bounce the user off-origin after the
 * Google round-trip.
 */
export function safeNext(raw: string | null | undefined): string {
  if (!raw) return "/";
  if (!raw.startsWith("/")) return "/";
  if (raw.startsWith("//")) return "/";
  if (raw.startsWith("/\\")) return "/";
  return raw;
}

export function SignInPage() {
  const { state, signIn } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = safeNext(params.get("next"));

  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);
  const buttonContainerRef = useRef<HTMLDivElement | null>(null);

  // Already signed in? Bounce to `next`. Effect rather than render guard so
  // we don't violate the rules of hooks below.
  useEffect(() => {
    if (state.status === "authenticated") {
      navigate(next, { replace: true });
    }
  }, [state.status, navigate, next]);

  const handleCredential = useCallback(
    async (resp: GoogleCredentialResponse) => {
      setError(null);
      setStatus("exchanging");
      try {
        const session = await api.auth.googleCallback(resp.credential);
        if (session.linkRequired) {
          setStatus("error");
          setError(LINK_REQUIRED_MESSAGE);
          return;
        }
        signIn(session);
        navigate(next, { replace: true });
      } catch (err) {
        setStatus("error");
        if (err instanceof ApiError) {
          setError(
            err.status === 401
              ? "Google sign-in was rejected. Please try again."
              : err.status === 503
                ? "Google sign-in is temporarily unavailable. Please try again."
                : err.message,
          );
        } else {
          setError("Could not complete sign-in. Please try again.");
        }
      }
    },
    [signIn, navigate, next],
  );

  const initAndRender = useCallback(
    (gis: Awaited<ReturnType<typeof loadGoogleIdentity>>) => {
      gis.initialize({
        client_id: GOOGLE_CLIENT_ID || "stub-client-id",
        callback: (resp) => {
          void handleCredential(resp);
        },
        ux_mode: "popup",
      });
      if (buttonContainerRef.current) {
        buttonContainerRef.current.replaceChildren();
        gis.renderButton(buttonContainerRef.current, {
          type: "standard",
          theme: "outline",
          size: "large",
          text: "signin_with",
          shape: "rectangular",
          logo_alignment: "left",
        });
      }
    },
    [handleCredential],
  );

  useEffect(() => {
    if (state.status !== "anonymous" && state.status !== "loading") return;
    let cancelled = false;
    setStatus("loading-sdk");
    loadGoogleIdentity()
      .then((gis) => {
        if (cancelled) return;
        if (!GOOGLE_CLIENT_ID && !window.__threadloopGoogleIdStub__) {
          setStatus("error");
          setError(
            "Google sign-in is not configured for this build. Set VITE_GOOGLE_CLIENT_ID and reload.",
          );
          return;
        }
        initAndRender(gis);
        setStatus("ready");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setStatus("error");
        setError(
          err instanceof Error
            ? `Could not load Google sign-in (${err.message}). Please retry.`
            : "Could not load Google sign-in. Please retry.",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [state.status, initAndRender]);

  const retry = useCallback(() => {
    setError(null);
    setStatus("loading-sdk");
    loadGoogleIdentity()
      .then((gis) => {
        initAndRender(gis);
        setStatus("ready");
      })
      .catch(() => {
        setStatus("error");
        setError("Could not load Google sign-in. Please retry.");
      });
  }, [initAndRender]);

  return (
    <main className="flex-1 max-w-md mx-auto w-full px-6 py-16">
      <section
        className="rounded-2xl border bg-white p-8 shadow-sm"
        aria-labelledby="sign-in-heading"
      >
        <h2 id="sign-in-heading" className="text-2xl font-semibold mb-2">
          Sign in to ThreadLoop
        </h2>
        <p className="text-neutral-600 mb-6">
          Use your Google account to continue. We never store passwords.
        </p>

        <div
          ref={buttonContainerRef}
          data-testid="google-button-container"
          className="min-h-[44px] flex items-center"
          aria-label="Sign in with Google"
        />

        {status === "loading-sdk" && (
          <p className="mt-4 text-sm text-neutral-500">Loading Google sign-in…</p>
        )}
        {status === "exchanging" && (
          <p className="mt-4 text-sm text-neutral-500">Completing sign-in…</p>
        )}

        <div
          role="alert"
          aria-live="assertive"
          data-testid="sign-in-error"
          className="mt-4 min-h-[1.5rem] text-sm text-rose-700"
        >
          {error}
        </div>

        {status === "error" && (
          <button
            type="button"
            onClick={retry}
            className="mt-4 inline-flex items-center justify-center rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm font-medium hover:bg-neutral-50 focus:outline-none focus:ring-2 focus:ring-brand"
          >
            Try again
          </button>
        )}

        {/* TODO(slice-2/#38): wire Apple + Facebook buttons here. */}
        <p className="mt-8 text-xs text-neutral-500">
          Apple and Facebook sign-in are coming soon.
        </p>
      </section>
    </main>
  );
}
