import { useCallback, useEffect, useRef, useState } from "react";
import {
  AccessibilityInfo,
  ActivityIndicator,
  InteractionManager,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  View,
  findNodeHandle,
} from "react-native";
import type { Text as RNText } from "react-native";
import type {
  AuthProvider,
  AuthenticatedSession,
  GoogleSsoCallbackInput,
  FacebookSsoCallbackInput,
} from "@threadloop/shared";
import { ApiError, api } from "../api/client";
import {
  extractFacebookAccessToken,
  useFacebookAuth,
} from "../auth/facebook";
import { extractGoogleIdToken, useGoogleAuth } from "../auth/google";

/**
 * Slice-4 account-linking modal for mobile. Mirrors the web modal
 * (`frontend-web/src/components/LinkAccountsDialog.tsx`) state machine
 * but uses RN primitives (`<Modal>`, `<Pressable>`) rather than the
 * web-only WAI-ARIA APG dialog hooks.
 *
 * Flow:
 *   1. Modal opens with the original-provider button highlighted.
 *   2. User taps the highlighted button → fresh `expo-auth-session`
 *      round-trip with that provider.
 *   3. On success, POST `{ linkToken, originalProvider, credential }`
 *      to `/api/auth/link`.
 *   4. On 200, hand off the merged `AuthenticatedSession` to `onLinked`
 *      — the parent screen promotes the session via `useAuth().signIn`.
 *
 * Failure mapping mirrors the web modal verbatim (which mirrors PR #64
 * "Failure mapping"):
 *   - 401: link token expired / consumed / mismatched / credential
 *     failed verification → single recovery message + "Back to sign-in".
 *   - 409: identity already linked to a different ThreadLoop account.
 *   - 503: provider temporarily unreachable.
 *
 * 10-minute client-side TTL matches the BE's default
 * `link_token_ttl_seconds=600`. Apple is never the original provider
 * in the active mobile build because `expo-apple-authentication` is
 * not bundled (Apple is descoped from Epic #11 — see
 * `docs/rfcs/0001-auth-sso.md` § "Deferred providers"). If the BE ever
 * returns `linkProvider: "apple"` on a mobile callback (impossible in
 * the active deployment per `docs/auth.md` § "Per-provider gating"),
 * the modal degrades to the expired-recovery state rather than
 * rendering a non-functional Apple button.
 */

const LINK_TOKEN_TTL_MS = 10 * 60 * 1000;

interface LinkAccountsModalProps {
  pendingLink: { token: string; provider: AuthProvider };
  onLinked: (session: AuthenticatedSession) => void;
  onClose: () => void;
}

type DialogStatus =
  | "idle"
  | "exchanging"
  | "expired"
  | "conflict"
  | "unreachable";

const PROVIDER_LABEL: Record<AuthProvider, string> = {
  google: "Google",
  apple: "Apple",
  facebook: "Facebook",
};

export function LinkAccountsModal({
  pendingLink,
  onLinked,
  onClose,
}: LinkAccountsModalProps) {
  const [status, setStatus] = useState<DialogStatus>("idle");
  const [statusMessage, setStatusMessage] = useState<string>("");

  const providerLabel = PROVIDER_LABEL[pendingLink.provider];
  const appleAsOriginal = pendingLink.provider === "apple";

  const google = useGoogleAuth();
  const facebook = useFacebookAuth();
  const lastClickedRef = useRef<AuthProvider | null>(null);
  const titleRef = useRef<RNText | null>(null);

  // ---- Move screen-reader focus to the dialog title on open ----
  // RN's <Modal> doesn't auto-shift screen-reader focus the way the web
  // <dialog> + focus-trap pattern does. iOS VoiceOver will otherwise
  // continue reading from wherever focus was before the modal mounted
  // (often a button now visually obscured but still in the a11y tree
  // underneath). Mirrors the web `LinkAccountsDialog` behaviour of
  // focusing the first focusable element on open. Runs after
  // interactions complete so iOS doesn't swallow the focus call during
  // the modal's fade animation.
  useEffect(() => {
    const handle = InteractionManager.runAfterInteractions(() => {
      const node = titleRef.current && findNodeHandle(titleRef.current);
      if (node != null) {
        AccessibilityInfo.setAccessibilityFocus(node);
      }
    });
    return () => {
      handle.cancel();
    };
  }, []);

  // ---- Failure mapping ----
  const handleLinkError = useCallback((err: unknown) => {
    if (err instanceof ApiError) {
      if (err.status === 409) {
        setStatus("conflict");
        setStatusMessage(
          "This account is already linked to a different ThreadLoop account. If you believe both accounts are yours, contact support.",
        );
        return;
      }
      if (err.status === 503) {
        setStatus("unreachable");
        setStatusMessage(
          "Couldn't reach the sign-in service just now. Please try again in a moment.",
        );
        return;
      }
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
      credential: GoogleSsoCallbackInput | FacebookSsoCallbackInput,
    ) => {
      setStatus("exchanging");
      setStatusMessage("Linking your accounts…");
      try {
        const session = await api.auth.link({
          linkToken: pendingLink.token,
          originalProvider: pendingLink.provider,
          credential,
        });
        if (session.linkRequired) {
          setStatus("expired");
          setStatusMessage(
            "Linking did not complete. Please sign in again to start over.",
          );
          return;
        }
        onLinked(session);
      } catch (err) {
        handleLinkError(err);
      }
    },
    [pendingLink, onLinked, handleLinkError],
  );

  // ---- Client-side TTL ----
  useEffect(() => {
    const timer = setTimeout(() => {
      setStatus("expired");
      setStatusMessage(
        "Your linking session expired. Please sign in again to start over.",
      );
    }, LINK_TOKEN_TTL_MS);
    return () => clearTimeout(timer);
  }, []);

  // ---- Original-provider auth-session response handlers ----
  useEffect(() => {
    if (lastClickedRef.current !== "google") return;
    if (!google.response) return;
    if (google.response.type === "success") {
      const idToken = extractGoogleIdToken(google.response);
      if (!idToken) {
        setStatus("unreachable");
        setStatusMessage("Could not start Google sign-in. Please try again.");
        return;
      }
      void postLink({ idToken });
      return;
    }
    if (google.response.type === "error") {
      setStatus("unreachable");
      setStatusMessage("Could not start Google sign-in. Please try again.");
    }
    // dismiss / cancel — leave status idle for retry.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [google.response]);

  useEffect(() => {
    if (lastClickedRef.current !== "facebook") return;
    if (!facebook.response) return;
    if (facebook.response.type === "success") {
      const accessToken = extractFacebookAccessToken(facebook.response);
      if (!accessToken) {
        setStatus("unreachable");
        setStatusMessage(
          "Could not start Facebook sign-in. Please try again.",
        );
        return;
      }
      void postLink({ accessToken });
      return;
    }
    if (facebook.response.type === "error") {
      setStatus("unreachable");
      setStatusMessage("Could not start Facebook sign-in. Please try again.");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [facebook.response]);

  const onGoogleReauth = async () => {
    lastClickedRef.current = "google";
    try {
      await google.promptAsync();
    } catch {
      setStatus("unreachable");
      setStatusMessage("Could not start Google sign-in. Please try again.");
    }
  };

  const onFacebookReauth = async () => {
    lastClickedRef.current = "facebook";
    try {
      await facebook.promptAsync();
    } catch {
      setStatus("unreachable");
      setStatusMessage("Could not start Facebook sign-in. Please try again.");
    }
  };

  const isRecovery =
    status === "expired" || status === "conflict" || status === "unreachable";

  return (
    <Modal
      visible
      transparent
      animationType="fade"
      onRequestClose={onClose}
      accessibilityViewIsModal
    >
      <View style={styles.overlay}>
        <View
          // NOTE: deliberately no `accessibilityRole="alert"` here — `alert`
          // is for ephemeral toast-like announcements, not labelled modal
          // dialogs (the web equivalent uses `role="dialog"`, not
          // `role="alert"`). On RN, `accessibilityViewIsModal` on the
          // parent `<Modal>` + this card's `accessibilityLabel` carry the
          // "this is a dialog you must dismiss" semantics. The polite-live
          // status `<Text>` below carries the alert role for transient
          // status announcements (`Linking your accounts…` / errors).
          accessibilityLabel="Link your accounts"
          style={styles.card}
          testID="link-accounts-modal"
        >
          <View style={styles.headerRow}>
            <Text
              ref={titleRef}
              accessibilityRole="header"
              style={styles.title}
            >
              Link your accounts
            </Text>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Close"
              onPress={onClose}
              hitSlop={12}
              style={styles.closeButton}
              testID="link-accounts-close"
            >
              <Text style={styles.closeButtonText}>×</Text>
            </Pressable>
          </View>

          <Text style={styles.body}>
            You already have a ThreadLoop account with{" "}
            <Text style={styles.bodyStrong}>{providerLabel}</Text>. To link
            them, sign in with {providerLabel} now.
          </Text>

          <Text style={styles.badge}>Original account</Text>

          {pendingLink.provider === "google" && (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Sign in with Google"
              disabled={status === "exchanging" || !google.request}
              onPress={onGoogleReauth}
              style={({ pressed }) => [
                styles.button,
                styles.googleButton,
                styles.highlightedButton,
                (pressed || status === "exchanging") && styles.buttonPressed,
              ]}
              testID="link-accounts-google"
            >
              {status === "exchanging" ? (
                <ActivityIndicator color="#1f2937" />
              ) : (
                <Text style={styles.googleButtonText}>
                  Sign in with Google
                </Text>
              )}
            </Pressable>
          )}

          {pendingLink.provider === "facebook" && (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Continue with Facebook"
              disabled={status === "exchanging" || !facebook.request}
              onPress={onFacebookReauth}
              style={({ pressed }) => [
                styles.button,
                styles.facebookButton,
                styles.highlightedButton,
                (pressed || status === "exchanging") && styles.buttonPressed,
              ]}
              testID="link-accounts-facebook"
            >
              {status === "exchanging" ? (
                <ActivityIndicator color="#ffffff" />
              ) : (
                <Text style={styles.facebookButtonText}>
                  Continue with Facebook
                </Text>
              )}
            </Pressable>
          )}

          {appleAsOriginal && (
            <Text style={styles.body}>
              Apple sign-in is not available in this build. Please contact
              support to resolve this account link.
            </Text>
          )}

          <Text
            accessibilityLiveRegion="polite"
            style={styles.status}
            testID="link-accounts-status"
          >
            {statusMessage}
          </Text>

          {(isRecovery || appleAsOriginal) && (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Back to sign-in"
              onPress={onClose}
              style={({ pressed }) => [
                styles.button,
                styles.secondaryButton,
                pressed && styles.buttonPressed,
              ]}
              testID="link-accounts-recovery"
            >
              <Text style={styles.secondaryButtonText}>Back to sign-in</Text>
            </Pressable>
          )}

          {!isRecovery && (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Cancel"
              onPress={onClose}
              style={({ pressed }) => [
                styles.button,
                styles.secondaryButton,
                pressed && styles.buttonPressed,
              ]}
              testID="link-accounts-cancel"
            >
              <Text style={styles.secondaryButtonText}>Cancel</Text>
            </Pressable>
          )}
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: "rgba(0, 0, 0, 0.5)",
    justifyContent: "center",
    padding: 16,
  },
  card: {
    backgroundColor: "#ffffff",
    borderRadius: 16,
    padding: 20,
    gap: 12,
  },
  headerRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  title: {
    fontSize: 18,
    fontWeight: "700",
    color: "#111827",
  },
  closeButton: {
    width: 44,
    height: 44,
    alignItems: "center",
    justifyContent: "center",
  },
  closeButtonText: {
    fontSize: 28,
    color: "#6b7280",
    lineHeight: 28,
  },
  body: {
    fontSize: 14,
    color: "#374151",
    lineHeight: 20,
  },
  bodyStrong: {
    fontWeight: "700",
    color: "#111827",
  },
  badge: {
    fontSize: 11,
    color: "#5b3df6",
    fontWeight: "700",
    letterSpacing: 0.5,
    textTransform: "uppercase",
    marginTop: 4,
  },
  button: {
    minHeight: 48,
    borderRadius: 8,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 16,
  },
  buttonPressed: {
    opacity: 0.85,
  },
  highlightedButton: {
    borderWidth: 2,
    borderColor: "#5b3df6",
  },
  googleButton: {
    backgroundColor: "#ffffff",
  },
  googleButtonText: {
    color: "#1f2937",
    fontWeight: "600",
    fontSize: 16,
  },
  facebookButton: {
    backgroundColor: "#1877f2",
  },
  facebookButtonText: {
    color: "#ffffff",
    fontWeight: "600",
    fontSize: 16,
  },
  secondaryButton: {
    backgroundColor: "#ffffff",
    borderWidth: 1,
    borderColor: "#d1d5db",
  },
  secondaryButtonText: {
    color: "#374151",
    fontWeight: "500",
    fontSize: 15,
  },
  status: {
    fontSize: 14,
    color: "#374151",
    minHeight: 20,
  },
});
