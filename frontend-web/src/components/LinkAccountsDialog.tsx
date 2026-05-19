import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import type { AuthProvider, AuthenticatedSession } from "@threadloop/shared";
import { ApiError, api } from "../api/client";
import {
  composeAppleDisplayName,
  loadAppleIdentity,
} from "../auth/apple";
import type { AppleSignInResponse } from "../auth/apple";
import { loadFacebookIdentity } from "../auth/facebook";
import type { FacebookLoginResponse } from "../auth/facebook";
import { loadGoogleIdentity } from "../auth/google";
import type { GoogleCredentialResponse } from "../auth/google";
import { useFocusTrap } from "../lib/focusTrap";

/**
 * Slice-4 (#40) account-linking modal — renders on top of `/sign-in` when
 * a callback returned `linkRequired: true`. The user's flow:
 *
 *   1. Modal opens explaining the collision (e.g. "You already have an
 *      account with Google — sign in with Google to link them").
 *   2. User clicks the highlighted original-provider button INSIDE the modal.
 *   3. The modal runs the original provider's SDK round-trip and posts the
 *      fresh credential to `POST /api/auth/link` with the `linkToken`.
 *   4. On 200 the merged session is handed off to `onLinked()` and the
 *      modal closes.
 *
 * Failure mapping (BE single 401 envelope per PR #64's "Failure mapping"):
 *   - 401: link token expired / consumed / mismatched / credential failed
 *     verification — we can't distinguish, surface "session expired, please
 *     start over" copy with a single "Back to sign-in" CTA.
 *   - 409: identity already linked to a different ThreadLoop account.
 *   - 503: provider temporarily unreachable.
 *
 * The `linkToken` lives in the parent `SignInPage`'s component state and is
 * passed in via prop — never persisted to localStorage / sessionStorage /
 * cookies. A page reload provably wipes it (modal is mounted on `/sign-in`,
 * not a separate route). A 10-minute client-side timer matching the BE's
 * default `link_token_ttl_seconds=600` proactively transitions the modal
 * to the expired state if the user lingers; the BE has already invalidated
 * the token by then, so any click after expiry would 401 anyway.
 */

const LINK_TOKEN_TTL_MS = 10 * 60 * 1000;

interface LinkAccountsDialogProps {
  /** The `{ linkToken, originalProvider }` carried from the failed callback. */
  pendingLink: { token: string; provider: AuthProvider };
  /**
   * Called when the link succeeds — receives the merged `AuthenticatedSession`
   * the BE returned from `POST /api/auth/link`. The parent is expected to
   * promote the session via `useAuth().signIn()` and navigate away.
   */
  onLinked: (session: AuthenticatedSession) => void;
  /**
   * Called when the user dismisses the modal (Esc, Cancel button, close X,
   * or after the expired-token-recovery CTA). The parent clears its
   * `pendingLink` state and restores focus to the originally-clicked
   * sign-in button (which the parent passes in via `triggerRef`).
   */
  onClose: () => void;
  /**
   * Ref to the originally-clicked second-provider button on `/sign-in`. Esc
   * (and any other dismissal path) restores focus there per the AC and the
   * WAI-ARIA APG dialog pattern.
   */
  triggerRef: React.RefObject<HTMLElement | null>;
  /**
   * Inline error surfaced when the user clicks the underlying second-provider
   * button while the modal is open (a "wrong provider" mistake). The parent
   * owns the message so it can clear it on its own state transitions.
   */
  wrongProviderMessage?: string | null;
  /** Clear `wrongProviderMessage` after the user acknowledges it. */
  onClearWrongProviderMessage?: () => void;
}

type DialogStatus =
  | "idle"
  | "exchanging"
  | "expired"
  | "conflict"
  | "unreachable"
  | "wrong-provider";

const PROVIDER_LABEL: Record<AuthProvider, string> = {
  google: "Google",
  apple: "Apple",
  facebook: "Facebook",
};

export function LinkAccountsDialog({
  pendingLink,
  onLinked,
  onClose,
  triggerRef,
  wrongProviderMessage,
  onClearWrongProviderMessage,
}: LinkAccountsDialogProps) {
  const headingId = useId();
  const bodyId = useId();
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const primaryButtonRef = useRef<HTMLButtonElement | null>(null);

  const [status, setStatus] = useState<DialogStatus>("idle");
  const [statusMessage, setStatusMessage] = useState<string>("");

  const providerLabel = PROVIDER_LABEL[pendingLink.provider];

  const dismiss = useCallback(() => {
    onClose();
    // Restore focus to the originally-clicked second-provider button. The
    // parent has already cleared `pendingLink` so the modal is unmounting
    // on this tick; defer the focus call to the next tick so the parent's
    // re-render lands first and the trigger element is reachable.
    queueMicrotask(() => {
      const trigger = triggerRef.current;
      if (!trigger) return;
      // Google's "trigger" is the GIS container <div>, which isn't itself
      // focusable — focus the first focusable descendant (typically the
      // GIS-rendered <div role="button">). Apple/Facebook trigger refs
      // point straight at the underlying <button> so the descendant
      // lookup is unnecessary; the fallback to `trigger.focus()` covers
      // them.
      const focusable = trigger.querySelector?.<HTMLElement>(
        'button, [role="button"], a[href], [tabindex]:not([tabindex="-1"])',
      );
      if (focusable) {
        focusable.focus();
        return;
      }
      if (typeof trigger.focus === "function") {
        trigger.focus();
      }
    });
  }, [onClose, triggerRef]);

  // Esc closes + restores focus. Bound at document-level rather than on
  // the dialog so it works even if focus has temporarily escaped (e.g.
  // during the SDK popup transition the active element may briefly be
  // `body`).
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        dismiss();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [dismiss]);

  // Trap focus inside the dialog while it's mounted. The hook is a no-op
  // when `active=false` so we don't have to mount/unmount it conditionally.
  useFocusTrap(dialogRef, true);

  // Initial focus: highlighted original-provider button on open, per the
  // WAI-ARIA APG dialog pattern (primary action wins focus; the close X
  // is secondary).
  useEffect(() => {
    primaryButtonRef.current?.focus();
  }, []);

  // Client-side TTL — matches the BE default `link_token_ttl_seconds=600`.
  // The token is dead at the BE before this fires, but a stale modal
  // claiming "click here to link" after expiry would be friction.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setStatus("expired");
      setStatusMessage(
        "Your linking session expired. Please sign in again to start over.",
      );
    }, LINK_TOKEN_TTL_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, []);

  const handleLinkSuccess = useCallback(
    (session: AuthenticatedSession) => {
      onLinked(session);
    },
    [onLinked],
  );

  const handleLinkError = useCallback((err: unknown) => {
    if (err instanceof ApiError) {
      if (err.status === 409) {
        setStatus("conflict");
        setStatusMessage(
          `This account is already linked to a different ThreadLoop account. If you believe both accounts are yours, contact support.`,
        );
        return;
      }
      if (err.status === 503) {
        setStatus("unreachable");
        setStatusMessage(
          `Couldn't reach the sign-in service just now. Please try again in a moment.`,
        );
        return;
      }
      // 401 (and anything else) collapses to the expired-or-mismatched
      // recovery state. Per PR #64 the BE deliberately doesn't distinguish
      // expired vs. replayed vs. mismatched-original-provider vs.
      // credential-failed-verification, so neither do we.
      setStatus("expired");
      setStatusMessage(
        "Your linking session expired. Please sign in again to start over.",
      );
      return;
    }
    setStatus("expired");
    setStatusMessage(
      "Could not complete linking. Please sign in again to start over.",
    );
  }, []);

  const postLink = useCallback(
    async (
      credential:
        | { idToken: string }
        | { idToken: string; code: string; name?: string }
        | { accessToken: string },
    ) => {
      onClearWrongProviderMessage?.();
      setStatus("exchanging");
      setStatusMessage("Linking your accounts…");
      try {
        const session = await api.auth.link({
          linkToken: pendingLink.token,
          originalProvider: pendingLink.provider,
          credential,
        });
        if (session.linkRequired) {
          // The link route should never return another link-required
          // envelope; treat as a hard failure and surface the recovery
          // copy. Defensive — the BE contract guarantees `linkRequired:
          // false` on a 200, but the type narrows here.
          setStatus("expired");
          setStatusMessage(
            "Linking did not complete. Please sign in again to start over.",
          );
          return;
        }
        handleLinkSuccess(session);
      } catch (err) {
        handleLinkError(err);
      }
    },
    [pendingLink, handleLinkSuccess, handleLinkError, onClearWrongProviderMessage],
  );

  // ---- Per-provider re-auth handlers ----
  //
  // The modal re-uses the existing per-provider SDK loaders rather than
  // pulling in a uniform abstraction; the brand-guideline forks per
  // provider make a uniform model awkward and three forks isn't enough
  // weight to justify the abstraction.

  const handleGoogleReauth = useCallback(async () => {
    if (status === "exchanging") return;
    try {
      const gis = await loadGoogleIdentity();
      // Use a one-shot credential capture — initialize with a callback that
      // closes over `postLink` so the credential round-trips through
      // `/api/auth/link` instead of `/api/auth/google/callback`.
      gis.initialize({
        client_id: import.meta.env.VITE_GOOGLE_CLIENT_ID || "stub-client-id",
        callback: (resp: GoogleCredentialResponse) => {
          void postLink({ idToken: resp.credential });
        },
        ux_mode: "popup",
      });
      // Render the GIS button into the modal-internal container; the user
      // will click it to trigger the popup. A `prompt()`-based path would
      // skip the click but Google forbids auto-prompting from a visible
      // page already showing the GIS button on the parent surface — keeps
      // the One Tap throttling honest.
      const container = primaryButtonRef.current?.parentElement;
      if (container) {
        container.replaceChildren();
        gis.renderButton(container, {
          type: "standard",
          theme: "outline",
          size: "large",
          text: "signin_with",
          shape: "rectangular",
          logo_alignment: "left",
        });
        // The fallback button (which the initial-focus effect targeted)
        // has just been replaced. Move focus to the GIS-rendered button
        // if we can find it, so the WAI-ARIA APG "primary action gets
        // initial focus" rule still holds when GIS rendering races the
        // first-paint focus effect.
        const focusable = container.querySelector<HTMLElement>(
          'button, [role="button"], [tabindex]:not([tabindex="-1"])',
        );
        focusable?.focus();
      }
    } catch {
      setStatus("unreachable");
      setStatusMessage("Could not load Google sign-in. Please try again.");
    }
  }, [status, postLink]);

  const handleAppleReauth = useCallback(async () => {
    if (status === "exchanging") return;
    try {
      const apple = await loadAppleIdentity();
      const resp: AppleSignInResponse = await apple.signIn();
      await postLink({
        idToken: resp.authorization.id_token,
        code: resp.authorization.code,
        name: composeAppleDisplayName(resp.user),
      });
    } catch (err: unknown) {
      // Apple user-cancel rejections — don't surface scary copy, just let
      // the user click again.
      if (err && typeof err === "object" && "error" in err) {
        const code = (err as { error?: unknown }).error;
        if (code === "popup_closed_by_user" || code === "user_cancelled_authorize") {
          return;
        }
      }
      setStatus("unreachable");
      setStatusMessage("Could not start Apple sign-in. Please try again.");
    }
  }, [status, postLink]);

  const handleFacebookReauth = useCallback(async () => {
    if (status === "exchanging") return;
    try {
      const fb = await loadFacebookIdentity();
      const resp = await new Promise<FacebookLoginResponse>((resolve) => {
        fb.login((r) => resolve(r), { scope: "email" });
      });
      if (resp.status !== "connected" || !resp.authResponse) {
        // User-cancel: stay on the modal, leave status idle so they can
        // click again. Mirrors the parent's Facebook handler.
        return;
      }
      await postLink({ accessToken: resp.authResponse.accessToken });
    } catch {
      setStatus("unreachable");
      setStatusMessage("Could not start Facebook sign-in. Please try again.");
    }
  }, [status, postLink]);

  // Wire the original-provider re-auth click through the right SDK. With
  // Google the button is rendered by GIS itself (we mount its container
  // and the user clicks the GIS-rendered button); for Apple and Facebook
  // we render our own Tailwind button that triggers the SDK on click.
  // Auto-mount the GIS render path on first paint so the user sees a real
  // Google button rather than a placeholder.
  useEffect(() => {
    if (pendingLink.provider === "google") {
      void handleGoogleReauth();
    }
    // Only run on first paint — re-running would re-mount the GIS button
    // (which is fine but wasteful). The SDK is a singleton inside its
    // loader so it won't be re-fetched.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The recovery states ("expired", "conflict", "unreachable") share the
  // same single-CTA shape but distinct copy + a "Back to sign-in" / "Try
  // again" choice; consolidate into one render branch.
  const isRecovery =
    status === "expired" || status === "conflict" || status === "wrong-provider";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4"
      data-testid="link-accounts-overlay"
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={headingId}
        aria-describedby={bodyId}
        data-testid="link-accounts-dialog"
        className="w-full max-w-md rounded-2xl border bg-white p-6 shadow-xl"
      >
        <div className="flex items-start justify-between gap-4">
          <h2
            id={headingId}
            className="text-xl font-semibold text-neutral-900"
          >
            Link your accounts
          </h2>
          <button
            type="button"
            onClick={dismiss}
            aria-label="Close"
            data-testid="link-accounts-close"
            className="min-h-[44px] min-w-[44px] inline-flex items-center justify-center rounded text-neutral-500 hover:text-neutral-900 focus:outline-none focus:ring-2 focus:ring-brand"
          >
            <span aria-hidden="true" className="text-xl leading-none">
              ×
            </span>
          </button>
        </div>

        <p id={bodyId} className="mt-3 text-sm text-neutral-700">
          You already have a ThreadLoop account with{" "}
          <strong>{providerLabel}</strong>. To link them, sign in with{" "}
          {providerLabel} now.
        </p>

        <div className="mt-5">
          <p
            className="mb-2 text-xs font-medium uppercase tracking-wide text-brand"
            data-testid="link-accounts-original-badge"
          >
            Original account
          </p>

          {pendingLink.provider === "google" && (
            <div
              className="rounded ring-2 ring-brand ring-offset-2 ring-offset-white"
              data-testid="link-accounts-original-button-wrapper"
            >
              {/*
               * GIS replaces the children of this container with its own
               * iframe-rendered button. The native button below acts as the
               * initial-focus target until GIS renders; once GIS renders,
               * focus is preserved on whatever the user tabs to. Without
               * this fallback the dialog has no focusable element on first
               * paint and the focus-trap has nothing to land on.
               */}
              <button
                ref={primaryButtonRef}
                type="button"
                data-testid="link-accounts-google-fallback"
                aria-label="Sign in with Google"
                className="w-full rounded border border-neutral-300 bg-white px-4 py-3 text-sm font-medium text-neutral-700 hover:bg-neutral-50 focus:outline-none focus:ring-2 focus:ring-brand"
                onClick={() => void handleGoogleReauth()}
              >
                Sign in with Google
              </button>
            </div>
          )}

          {pendingLink.provider === "apple" && (
            <div
              className="rounded ring-2 ring-brand ring-offset-2 ring-offset-white"
              data-testid="link-accounts-original-button-wrapper"
            >
              <button
                ref={primaryButtonRef}
                type="button"
                onClick={() => void handleAppleReauth()}
                disabled={status === "exchanging"}
                data-testid="link-accounts-apple-button"
                aria-label="Sign in with Apple"
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

          {pendingLink.provider === "facebook" && (
            <div
              className="rounded ring-2 ring-brand ring-offset-2 ring-offset-white"
              data-testid="link-accounts-original-button-wrapper"
            >
              <button
                ref={primaryButtonRef}
                type="button"
                onClick={() => void handleFacebookReauth()}
                disabled={status === "exchanging"}
                data-testid="link-accounts-facebook-button"
                aria-label="Continue with Facebook"
                className="w-full inline-flex items-center justify-center gap-2 rounded bg-facebook px-4 py-3 text-sm font-medium text-white shadow-sm hover:bg-facebook-dark focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-facebook disabled:opacity-60 disabled:cursor-not-allowed"
              >
                <svg
                  aria-hidden="true"
                  focusable="false"
                  viewBox="0 0 16 16"
                  className="h-[18px] w-[18px]"
                  fill="currentColor"
                >
                  <path d="M16 8.049c0-4.446-3.582-8.05-8-8.05C3.58 0-.002 3.603-.002 8.05c0 4.017 2.926 7.347 6.75 7.951v-5.625h-2.03V8.05H6.75V6.275c0-2.017 1.195-3.131 3.022-3.131.876 0 1.791.157 1.791.157v1.98h-1.009c-.993 0-1.303.621-1.303 1.258v1.51h2.218l-.354 2.326H9.25V16c3.824-.604 6.75-3.934 6.75-7.951" />
                </svg>
                <span>Continue with Facebook</span>
              </button>
            </div>
          )}
        </div>

        <div className="mt-4 flex justify-between gap-3">
          <button
            type="button"
            onClick={dismiss}
            data-testid="link-accounts-cancel"
            className="rounded border border-neutral-300 bg-white px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 focus:outline-none focus:ring-2 focus:ring-brand"
          >
            Cancel
          </button>
        </div>

        {/*
         * Status region for SDK-flow announcements. Distinct from the
         * page-level `aria-live="assertive"` error region on SignInPage
         * (`data-testid="sign-in-error"`) — this one is `polite` so JAWS /
         * NVDA don't interrupt mid-sentence during the SDK round-trip.
         * Empty by default; populated by status transitions.
         */}
        <div
          role="status"
          aria-live="polite"
          data-testid="link-accounts-status"
          className="mt-4 min-h-[1.5rem] text-sm text-neutral-700"
        >
          {wrongProviderMessage ?? statusMessage}
        </div>

        {isRecovery && (
          <div className="mt-2">
            <button
              type="button"
              onClick={dismiss}
              data-testid="link-accounts-recovery-cta"
              className="inline-flex items-center justify-center rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm font-medium hover:bg-neutral-50 focus:outline-none focus:ring-2 focus:ring-brand"
            >
              Back to sign-in
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
