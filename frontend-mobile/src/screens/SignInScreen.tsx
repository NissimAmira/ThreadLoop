import { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import type { AuthProvider, Session } from "@threadloop/shared";
import { ApiError, api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import {
  extractFacebookAccessToken,
  isFacebookConfigured,
  useFacebookAuth,
} from "../auth/facebook";
import {
  extractGoogleIdToken,
  isGoogleConfigured,
  useGoogleAuth,
} from "../auth/google";
import { config } from "../config/env";
import { LinkAccountsModal } from "../components/LinkAccountsModal";

/**
 * Sign-in screen — slice 5 mobile equivalent of
 * `frontend-web/src/pages/SignInPage.tsx`.
 *
 * Renders one button per active provider — Google + Facebook by
 * default. Apple is descoped from Epic #11 per
 * `docs/rfcs/0001-auth-sso.md` § "Deferred providers"; the button is
 * not rendered and `expo-apple-authentication` is not bundled. The
 * Apple gate exists in `config.appleEnabled` for symmetry with web,
 * but flipping it alone won't surface a button — re-activation is
 * tracked in Epic #57.
 *
 * Per-provider flow:
 *   1. User taps a provider button → `promptAsync()` from
 *      `expo-auth-session`, which launches the in-app browser tab.
 *   2. On the auth-session response, extract the provider credential
 *      (Google: `id_token`, Facebook: `accessToken`) and POST to the
 *      matching `/api/auth/{provider}/callback`.
 *   3. On a happy `Session`, hand off to `useAuth().signIn()` — the
 *      RootNavigator switches to `MeScreen` automatically.
 *   4. On `linkRequired`, open `LinkAccountsModal` with the link
 *      token + original provider. Mirrors the web modal behaviour.
 *
 * `linkToken` lives in component state for the duration of the
 * pending-link flow — never persisted to secure store. Killing the
 * screen (back out, app backgrounded long enough to dehydrate) clears
 * it, matching the web client's "reload provably wipes it" semantic.
 */

interface PendingLink {
  token: string;
  provider: AuthProvider;
}

type ButtonStatus = "idle" | "loading";

export function SignInScreen() {
  const { signIn } = useAuth();
  const [googleStatus, setGoogleStatus] = useState<ButtonStatus>("idle");
  const [facebookStatus, setFacebookStatus] = useState<ButtonStatus>("idle");
  const [pendingLink, setPendingLink] = useState<PendingLink | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Track which provider button the user actually pressed so we can
  // ignore stale response transitions when re-mounting (e.g. after
  // returning from a backgrounded in-app browser tab on Android).
  const lastClickedProvider = useRef<AuthProvider | null>(null);

  const google = useGoogleAuth();
  const facebook = useFacebookAuth();

  const handleSession = (session: Session, originalProvider: AuthProvider) => {
    if (session.linkRequired) {
      setPendingLink({
        token: session.linkToken,
        provider: session.linkProvider,
      });
      // Reset the just-clicked button state so a subsequent cancel
      // from the modal returns the screen to a clickable state.
      if (originalProvider === "google") setGoogleStatus("idle");
      else if (originalProvider === "facebook") setFacebookStatus("idle");
      return;
    }
    signIn(session);
  };

  const handleApiError = (err: unknown, providerLabel: string) => {
    if (err instanceof ApiError) {
      if (err.status === 401) {
        setError(
          `${providerLabel} couldn't verify your sign-in. Please try again.`,
        );
        return;
      }
      if (err.status === 503) {
        setError(
          `${providerLabel} sign-in is temporarily unreachable. Please try again in a few minutes.`,
        );
        return;
      }
      setError(
        `${providerLabel} sign-in failed (${err.status}). Please try again.`,
      );
      return;
    }
    setError(`${providerLabel} sign-in failed unexpectedly. Please try again.`);
  };

  // React to the Google auth-session response.
  useEffect(() => {
    if (lastClickedProvider.current !== "google") return;
    if (!google.response) return;

    if (google.response.type === "success") {
      const idToken = extractGoogleIdToken(google.response);
      if (!idToken) {
        setGoogleStatus("idle");
        setError("Google did not return an ID token. Please try again.");
        return;
      }
      void (async () => {
        try {
          const session = await api.auth.googleCallback({ idToken });
          handleSession(session, "google");
        } catch (err) {
          handleApiError(err, "Google");
        } finally {
          setGoogleStatus("idle");
        }
      })();
      return;
    }
    if (google.response.type === "error") {
      setGoogleStatus("idle");
      setError("Could not start Google sign-in. Please try again.");
      return;
    }
    // `dismiss` / `cancel` — silent revert, no error UI.
    setGoogleStatus("idle");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [google.response]);

  // React to the Facebook auth-session response.
  useEffect(() => {
    if (lastClickedProvider.current !== "facebook") return;
    if (!facebook.response) return;

    if (facebook.response.type === "success") {
      const accessToken = extractFacebookAccessToken(facebook.response);
      if (!accessToken) {
        setFacebookStatus("idle");
        setError(
          "Facebook did not return an access token. Please try again.",
        );
        return;
      }
      void (async () => {
        try {
          const session = await api.auth.facebookCallback({ accessToken });
          handleSession(session, "facebook");
        } catch (err) {
          handleApiError(err, "Facebook");
        } finally {
          setFacebookStatus("idle");
        }
      })();
      return;
    }
    if (facebook.response.type === "error") {
      setFacebookStatus("idle");
      setError("Could not start Facebook sign-in. Please try again.");
      return;
    }
    setFacebookStatus("idle");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [facebook.response]);

  const onGooglePress = async () => {
    setError(null);
    if (!isGoogleConfigured()) {
      Alert.alert(
        "Google sign-in not configured",
        "This build does not have a Google OAuth client ID for the current platform. See frontend-mobile/README.md.",
      );
      return;
    }
    lastClickedProvider.current = "google";
    setGoogleStatus("loading");
    try {
      await google.promptAsync();
    } catch {
      setGoogleStatus("idle");
      setError("Could not launch Google sign-in. Please try again.");
    }
  };

  const onFacebookPress = async () => {
    setError(null);
    if (!isFacebookConfigured()) {
      Alert.alert(
        "Facebook sign-in not configured",
        "This build does not have a Facebook App ID. See frontend-mobile/README.md.",
      );
      return;
    }
    lastClickedProvider.current = "facebook";
    setFacebookStatus("loading");
    try {
      await facebook.promptAsync();
    } catch {
      setFacebookStatus("idle");
      setError("Could not launch Facebook sign-in. Please try again.");
    }
  };

  const showGoogle = config.googleEnabled;
  const showFacebook = config.facebookEnabled;
  const noProvidersAvailable = !showGoogle && !showFacebook;

  return (
    <View style={styles.root}>
      <View style={styles.card}>
        <Text accessibilityRole="header" style={styles.title}>
          Sign in to ThreadLoop
        </Text>
        <Text style={styles.subtitle}>
          Choose a provider to continue. We never see your password.
        </Text>

        {noProvidersAvailable && (
          <Text style={styles.empty} accessibilityLiveRegion="polite">
            Sign-in is currently unavailable. Please try again later.
          </Text>
        )}

        {showGoogle && (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Sign in with Google"
            accessibilityState={{
              disabled: googleStatus === "loading" || !google.request,
            }}
            disabled={googleStatus === "loading" || !google.request}
            onPress={onGooglePress}
            style={({ pressed }) => [
              styles.button,
              styles.googleButton,
              (pressed || googleStatus === "loading") && styles.buttonPressed,
            ]}
            testID="sign-in-google"
          >
            {googleStatus === "loading" ? (
              <ActivityIndicator color="#1f2937" />
            ) : (
              <Text style={styles.googleButtonText}>Sign in with Google</Text>
            )}
          </Pressable>
        )}

        {showFacebook && (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Continue with Facebook"
            accessibilityState={{
              disabled: facebookStatus === "loading" || !facebook.request,
            }}
            disabled={facebookStatus === "loading" || !facebook.request}
            onPress={onFacebookPress}
            style={({ pressed }) => [
              styles.button,
              styles.facebookButton,
              (pressed || facebookStatus === "loading") &&
                styles.buttonPressed,
            ]}
            testID="sign-in-facebook"
          >
            {facebookStatus === "loading" ? (
              <ActivityIndicator color="#ffffff" />
            ) : (
              <Text style={styles.facebookButtonText}>
                Continue with Facebook
              </Text>
            )}
          </Pressable>
        )}

        {error && (
          <Text
            accessibilityLiveRegion="assertive"
            style={styles.error}
            testID="sign-in-error"
          >
            {error}
          </Text>
        )}
      </View>

      {pendingLink && (
        <LinkAccountsModal
          pendingLink={pendingLink}
          onLinked={(session) => {
            setPendingLink(null);
            signIn(session);
          }}
          onClose={() => setPendingLink(null)}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: "#fafafa",
    padding: 24,
    justifyContent: "center",
  },
  card: {
    backgroundColor: "#ffffff",
    borderRadius: 16,
    padding: 24,
    gap: 16,
    shadowColor: "#000",
    shadowOpacity: 0.05,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  title: {
    fontSize: 22,
    fontWeight: "700",
    color: "#111827",
  },
  subtitle: {
    fontSize: 14,
    color: "#4b5563",
  },
  empty: {
    fontSize: 14,
    color: "#6b7280",
    fontStyle: "italic",
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
  googleButton: {
    backgroundColor: "#ffffff",
    borderWidth: 1,
    borderColor: "#d1d5db",
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
  error: {
    color: "#b91c1c",
    fontSize: 14,
    marginTop: 4,
  },
});
