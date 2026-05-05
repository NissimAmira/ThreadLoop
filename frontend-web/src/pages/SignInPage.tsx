import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ApiError, api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { loadGoogleIdentity } from "../auth/google";
import type { GoogleCredentialResponse } from "../auth/google";
import { composeAppleDisplayName, loadAppleIdentity } from "../auth/apple";
import type { AppleSignInResponse } from "../auth/apple";

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID ?? "";
const APPLE_CLIENT_ID = import.meta.env.VITE_APPLE_CLIENT_ID ?? "";
const APPLE_REDIRECT_URI = import.meta.env.VITE_APPLE_REDIRECT_URI ?? "";
const LINK_REQUIRED_MESSAGE =
  "This email is registered with another provider; please sign in with that provider instead.";

// Visual cap applied to both providers' buttons so they line up on
// `/sign-in` at desktop widths AND fill the column on narrow viewports
// (iPhone SE / 375px would overflow a hard 320px pin by ~57px inside this
// card's `p-8` + `px-6`). Treat as a max, not a hard width: Apple uses
// `w-full` + `style={{ maxWidth }}` so the button shrinks below 320px when
// the parent does, and GIS is intentionally NOT given a `width` option so
// it intrinsic-sizes within its container — both buttons are bounded by
// the same parent column, so width parity holds at every viewport.
const PROVIDER_BUTTON_MAX_WIDTH_PX = 320;

// `"hidden"` is the prod-mode response to a missing `VITE_*_CLIENT_ID`:
// rather than show end users a developer-flavoured error, drop the button
// entirely. In DEV the actionable error stays so misconfigured local stacks
// remain obvious to engineers. See docs/auth.md § Per-provider gating.
type Status =
  | "idle"
  | "loading-sdk"
  | "ready"
  | "exchanging"
  | "error"
  | "hidden";
type AppleStatus = "idle" | "loading-sdk" | "ready" | "error" | "hidden";

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

  const [appleStatus, setAppleStatus] = useState<AppleStatus>("idle");
  const [appleBusy, setAppleBusy] = useState(false);

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

  // Latest credential handler in a ref so initAndRender doesn't have to
  // re-bind (and re-render the button) every time `next` or `signIn` change
  // identity. The GIS callback closes over the ref, so it always sees the
  // latest handler.
  const handleCredentialRef = useRef(handleCredential);
  useEffect(() => {
    handleCredentialRef.current = handleCredential;
  }, [handleCredential]);

  const initAndRender = useCallback((gis: Awaited<ReturnType<typeof loadGoogleIdentity>>) => {
    gis.initialize({
      client_id: GOOGLE_CLIENT_ID || "stub-client-id",
      callback: (resp) => {
        void handleCredentialRef.current(resp);
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
        // No `width` option: a hard pin overflows a 375px viewport (iPhone
        // SE) inside this card. GIS intrinsic-sizes within its container
        // instead, and the parent column caps both buttons at
        // PROVIDER_BUTTON_MAX_WIDTH_PX on wider viewports.
      });
    }
  }, []);

  useEffect(() => {
    if (state.status === "authenticated") return;
    let cancelled = false;
    setStatus("loading-sdk");
    loadGoogleIdentity()
      .then((gis) => {
        if (cancelled) return;
        if (!GOOGLE_CLIENT_ID && !window.__threadloopGoogleIdStub__) {
          // Slice-N-only deployments unset VITE_GOOGLE_CLIENT_ID to drop the
          // Google button. Prod users see no button (matches the
          // docs/auth.md § Per-provider gating intent: "the sign-in page
          // renders that provider's button disabled with no scary error").
          // DEV mode keeps the actionable error so a misconfigured local
          // stack is loud, not silent.
          if (import.meta.env.DEV) {
            setStatus("error");
            setError(
              "Google sign-in is not configured for this build. Set VITE_GOOGLE_CLIENT_ID and reload.",
            );
          } else {
            setStatus("hidden");
          }
          return;
        }
        initAndRender(gis);
        setStatus("ready");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setStatus("error");
        // Only the dev-flavoured tail (the underlying error message) is
        // gated on DEV; the user-facing message stays the same.
        if (import.meta.env.DEV && err instanceof Error) {
          setError(
            `Could not load Google sign-in (${err.message}). Please retry.`,
          );
        } else {
          setError("Could not load Google sign-in. Please retry.");
        }
      });
    return () => {
      cancelled = true;
    };
    // Intentionally not depending on state.status: we want to render the
    // button exactly once on mount. A transition to `authenticated` is
    // handled by the redirect effect above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initAndRender]);

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

  // ---- Apple (slice 2 / #38) ----

  const handleAppleResponse = useCallback(
    async (resp: AppleSignInResponse) => {
      setError(null);
      try {
        const session = await api.auth.appleCallback({
          idToken: resp.authorization.id_token,
          code: resp.authorization.code,
          name: composeAppleDisplayName(resp.user),
        });
        if (session.linkRequired) {
          setError(LINK_REQUIRED_MESSAGE);
          return;
        }
        signIn(session);
        navigate(next, { replace: true });
      } catch (err) {
        if (err instanceof ApiError) {
          setError(
            err.status === 401
              ? "Apple sign-in was rejected. Please try again."
              : err.status === 503
                ? "Apple sign-in is temporarily unavailable. Please try again."
                : err.message,
          );
        } else {
          setError("Could not complete sign-in. Please try again.");
        }
      }
    },
    [signIn, navigate, next],
  );

  // Init Apple on mount. The official SDK exposes `init(...)` that has to fire
  // before `signIn()`. We don't render Apple's own button (the SDK's
  // declarative `<div id="appleid-signin">` requires the script to be loaded
  // before the markup paints, which fights React's render order); we render a
  // plain Tailwind-styled button that calls `signIn()` on click and
  // approximates Apple's brand guidelines (black bg, white text, Apple
  // glyph, "Sign in with Apple" text). The official brand-asset mark
  // ships pre-mobile-review (#20).
  useEffect(() => {
    if (state.status === "authenticated") return;
    let cancelled = false;
    setAppleStatus("loading-sdk");
    loadAppleIdentity()
      .then((apple) => {
        if (cancelled) return;
        if (
          !APPLE_CLIENT_ID &&
          !window.__threadloopAppleIdStub__
        ) {
          // Slice-1-only deployments unset VITE_APPLE_CLIENT_ID to drop the
          // Apple button. Prod users see no button (matches the
          // docs/auth.md § Per-provider gating intent). DEV mode keeps the
          // actionable error so a misconfigured local stack is loud, not
          // silent. Symmetric with the Google path above.
          if (import.meta.env.DEV) {
            setAppleStatus("error");
            setError(
              "Apple sign-in is not configured for this build. Set VITE_APPLE_CLIENT_ID and reload.",
            );
          } else {
            setAppleStatus("hidden");
          }
          return;
        }
        try {
          apple.init({
            clientId: APPLE_CLIENT_ID || "stub-apple-client-id",
            scope: "name email",
            redirectURI: APPLE_REDIRECT_URI || window.location.origin,
            usePopup: true,
          });
          setAppleStatus("ready");
        } catch {
          setAppleStatus("error");
          setError("Could not initialize Apple sign-in. Please retry.");
        }
      })
      .catch(() => {
        if (cancelled) return;
        setAppleStatus("error");
        setError("Could not load Apple sign-in. Please retry.");
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleAppleClick = useCallback(async () => {
    if (appleStatus !== "ready" || appleBusy) return;
    setAppleBusy(true);
    setError(null);
    try {
      const apple = await loadAppleIdentity();
      const resp = await apple.signIn();
      await handleAppleResponse(resp);
    } catch (err: unknown) {
      // Apple rejects on user-cancel with `{ error: "popup_closed_by_user" }`
      // or similar; treat all SDK rejections as "the user didn't complete it"
      // and surface a retry-able message rather than a scary failure.
      if (err && typeof err === "object" && "error" in err) {
        const code = (err as { error?: unknown }).error;
        if (code === "popup_closed_by_user" || code === "user_cancelled_authorize") {
          // User-cancelled — leave error empty so they can just click again.
          return;
        }
      }
      setError("Could not start Apple sign-in. Please try again.");
    } finally {
      setAppleBusy(false);
    }
  }, [appleStatus, appleBusy, handleAppleResponse]);

  const appleDisabled = appleStatus !== "ready" || appleBusy;

  // When every provider's button is hidden in prod (slice-N-only deployments
  // where neither `VITE_GOOGLE_CLIENT_ID` nor `VITE_APPLE_CLIENT_ID` are set),
  // the buttons section would otherwise render as blank space below the
  // "continue with one of the providers below" prose. Substitute a graceful
  // empty state so users don't read the page as broken. DEV builds keep the
  // actionable per-provider error messages and do not enter this branch
  // (one of `status` / `appleStatus` will be `"error"`, not `"hidden"`).
  const allHidden = status === "hidden" && appleStatus === "hidden";

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
          Continue with one of the providers below. We never store passwords.
        </p>

        {allHidden && (
          <p
            data-testid="sign-in-unavailable"
            className="text-neutral-600"
          >
            Sign-in is currently unavailable. Please try again later.
          </p>
        )}

        {status !== "hidden" && (
          <div
            ref={buttonContainerRef}
            data-testid="google-button-container"
            className="min-h-[44px] flex items-center"
            aria-label="Sign in with Google"
          />
        )}

        {appleStatus !== "hidden" && (
          <div className="mt-3">
            <button
              type="button"
              onClick={() => void handleAppleClick()}
              disabled={appleDisabled}
              data-testid="apple-signin-button"
              aria-label="Sign in with Apple"
              style={{ maxWidth: PROVIDER_BUTTON_MAX_WIDTH_PX }}
              className="w-full inline-flex items-center justify-center gap-2 rounded bg-black px-4 py-3 text-sm font-medium text-white shadow-sm hover:bg-neutral-900 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-black disabled:opacity-60 disabled:cursor-not-allowed"
            >
              <svg
                aria-hidden="true"
                focusable="false"
                viewBox="0 0 16 16"
                className="h-[18px] w-[18px]"
                fill="currentColor"
              >
                <path d="M11.182.008C11.148-.03 9.923.023 8.857 1.18c-1.066 1.156-.902 2.482-.878 2.516.024.034 1.52.087 2.475-1.258.955-1.345.762-2.391.728-2.43Zm3.314 11.733c-.048-.096-2.325-1.234-2.113-3.422.212-2.189 1.675-2.789 1.698-2.854.023-.065-.597-.79-1.254-1.157a3.692 3.692 0 0 0-1.563-.434c-.108-.003-.483-.095-1.254.116-.508.139-1.653.589-1.968.607-.316.018-1.256-.522-2.267-.665-.647-.125-1.333.131-1.824.328-.49.196-1.422.754-2.074 2.237-.652 1.482-.311 3.83-.067 4.56.244.729.625 1.924 1.273 2.796.576.984 1.34 1.667 1.659 1.899.319.232 1.219.386 1.843.067.502-.308 1.408-.485 1.766-.472.357.013 1.061.154 1.782.539.571.197 1.111.115 1.652-.105.541-.221 1.324-1.059 2.238-2.758.347-.79.505-1.217.473-1.282Z" />
              </svg>
              <span>Sign in with Apple</span>
            </button>
          </div>
        )}

        {status === "loading-sdk" && (
          <p className="mt-4 text-sm text-neutral-500">Loading Google sign-in…</p>
        )}
        {status === "exchanging" && (
          <p className="mt-4 text-sm text-neutral-500">Completing sign-in…</p>
        )}
        {appleBusy && (
          <p className="mt-4 text-sm text-neutral-500" data-testid="apple-busy">
            Completing Apple sign-in…
          </p>
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

        {/* TODO(slice-3/#39): wire Facebook button here. */}
        <p className="mt-8 text-xs text-neutral-500">
          Facebook sign-in is coming soon.
        </p>
      </section>
    </main>
  );
}
