# ThreadLoop Mobile (Expo)

Expo React Native client for ThreadLoop. Currently covers the SSO
sign-in flow (slice 5 of Epic #11) for Google + Facebook on iOS and
Android.

## Quick start

```bash
npm install
cp .env.example .env       # then fill in client IDs / app ID
npm start                  # then press i (iOS) / a (Android) / w (web)
```

On a physical device, replace `localhost` in `.env` with your machine's
LAN IP so the device can reach the backend container. On the Android
emulator, use `http://10.0.2.2:8000` — the emulator routes that to the
host loopback automatically.

## Scripts

| Command            | Purpose                                                   |
| ------------------ | --------------------------------------------------------- |
| `npm start`        | Boot the Metro bundler.                                   |
| `npm run ios`      | Boot Metro and open the iOS simulator.                    |
| `npm run android`  | Boot Metro and open the Android emulator.                 |
| `npm run web`      | Boot Metro and serve via web (limited — see § Web below). |
| `npm run typecheck`| `tsc --noEmit`.                                           |
| `npm test`         | Jest unit tests (`jest-expo` preset).                     |

## Sign-in setup

The sign-in surface ships with two providers in this Epic: Google and
Facebook. Apple is descoped per
[`docs/rfcs/0001-auth-sso.md` § "Deferred providers"](../docs/rfcs/0001-auth-sso.md#deferred-providers)
and re-enters scope with the App Store submission Epic (#57). The
`EXPO_PUBLIC_APPLE_ENABLED` flag exists for symmetry with the web
client but flipping it alone won't surface a button —
`expo-apple-authentication` is not bundled in this build.

### 1. Google sign-in

Google Cloud Console → **APIs & Services** → **Credentials** →
**Create credentials** → **OAuth client ID**. Create two separate
clients (Google issues one credential per platform):

**iOS client**
- Application type: **iOS application**.
- Bundle ID: `com.threadloop.app` (matches
  `app.json -> ios.bundleIdentifier`).
- Set `EXPO_PUBLIC_GOOGLE_CLIENT_ID_IOS` to the resulting client ID.

**Android client**
- Application type: **Android application**.
- Package name: `com.threadloop.app` (matches
  `app.json -> android.package`).
- SHA-1 fingerprint: the one Expo Go / your dev client uses. For Expo
  Go, see Expo's
  [Google Cloud Console for Expo Go SHA-1 fingerprint docs](https://docs.expo.dev/guides/google-authentication/#android).
  For EAS-built dev / preview / production binaries, run
  `eas credentials -p android` and read the value from there.
- Set `EXPO_PUBLIC_GOOGLE_CLIENT_ID_ANDROID` to the resulting client
  ID.

The backend `GOOGLE_CLIENT_ID` (web client ID from the same project)
must also be set — the BE validates the ID token's `aud` claim against
it. Mobile and BE client IDs are different OAuth credentials from the
same Google Cloud project; the BE `aud` check accepts whichever of the
project's client IDs minted the token (see `docs/auth.md`
§ "Google specifics").

### 2. Facebook sign-in

[developers.facebook.com](https://developers.facebook.com) → **My
Apps** → **Create App** → **Consumer** use case. Then:

- Add the **Facebook Login for iOS** product. iOS bundle ID:
  `com.threadloop.app`.
- Add the **Facebook Login for Android** product. Android package:
  `com.threadloop.app`. Key hashes: the Expo / EAS-managed debug key
  hash (Meta's dashboard surfaces the exact command — typically
  `keytool -exportcert -alias androiddebugkey -keystore
  ~/.android/debug.keystore | openssl sha1 -binary | openssl base64`).
- Copy the **App ID** into `EXPO_PUBLIC_FACEBOOK_APP_ID` here AND into
  `FACEBOOK_APP_ID` on the backend — they must match exactly because
  the BE's `/debug_token` verifier checks `data.app_id` against the
  backend value.

### 3. Environment variables

Copy `.env.example` to `.env` and fill in the values. Strict
`=== "true"` parse on every gating flag — anything other than the
literal string `true` (including unset) resolves to `false`.

| Variable                                  | Purpose                                                         |
| ----------------------------------------- | --------------------------------------------------------------- |
| `EXPO_PUBLIC_API_BASE_URL`                | Backend base URL. Default `http://localhost:8000`.              |
| `EXPO_PUBLIC_GOOGLE_ENABLED`              | Show the Google button. Default `true`.                         |
| `EXPO_PUBLIC_FACEBOOK_ENABLED`            | Show the Facebook button. Default `true`.                       |
| `EXPO_PUBLIC_APPLE_ENABLED`               | Apple gate — must stay `false` until Epic #57 lands.            |
| `EXPO_PUBLIC_GOOGLE_CLIENT_ID_IOS`        | Google OAuth iOS client ID.                                     |
| `EXPO_PUBLIC_GOOGLE_CLIENT_ID_ANDROID`    | Google OAuth Android client ID.                                 |
| `EXPO_PUBLIC_FACEBOOK_APP_ID`             | Meta App ID (must equal backend `FACEBOOK_APP_ID`).             |

### 4. Backend feature flags

The mobile app talks to the same `/api/auth/*` endpoints the web app
uses. The backend must boot with the matching gating flags set —
typically `AUTH_ENABLED=true`, `GOOGLE_ENABLED=true`,
`FACEBOOK_ENABLED=true`, plus the corresponding secrets. See
`docs/auth.md` § "Per-provider gating" for the full matrix.

## Architecture

```
App.tsx
└── SafeAreaProvider
    └── AuthProvider          (src/auth/AuthContext.tsx)
        └── RootNavigator     (src/navigation/RootNavigator.tsx)
            ├── SignInScreen  (anonymous)
            └── MeScreen      (authenticated)
```

- `src/auth/AuthContext.tsx` — three-state machine
  (`loading` / `anonymous` / `authenticated`), silent-refresh on
  mount via `POST /api/auth/refresh`, fallback to stored access token
  + `/api/me` on network failure. Mirrors
  `frontend-web/src/auth/AuthContext.tsx`.
- `src/auth/secureStore.ts` — `expo-secure-store` wrapper. The access
  JWT lives in memory for hot paths and is mirrored into the platform
  secure store so cold-starts hydrate the user view without waiting
  for the refresh round-trip. The refresh token is the httpOnly
  cookie set by the backend; React Native's `fetch` cookie jar handles
  it transparently with `credentials: "include"`.
- `src/auth/google.ts` / `src/auth/facebook.ts` — thin wrappers around
  `expo-auth-session`'s Google / Facebook providers. Extract the
  appropriate credential (ID token for Google, access token for
  Facebook) from the `useAuthRequest` response.
- `src/api/client.ts` — typed HTTP client mirroring
  `frontend-web/src/api/client.ts`. camelCase wire end-to-end per
  ADR 0009; consumes `@threadloop/shared` types directly.
- `src/components/LinkAccountsModal.tsx` — slice-4 account-linking
  modal. Mirrors `frontend-web/src/components/LinkAccountsDialog.tsx`
  state machine using RN primitives. 401 / 409 / 503 failure mapping,
  10-minute client-side TTL, original-provider re-auth.

## Testing

```bash
npm run typecheck
npm test                    # jest-expo unit tests
```

Unit tests cover the AuthContext state transitions (silent-refresh,
401, link-required defensive, network-failure fallback, signIn,
signOut). Detox E2E is per-release, not per-PR — set up under the
mobile e2e Epic (not yet started).

When validating the sign-in flow manually:

1. Start the backend stack: `make dev` from the repo root.
2. `cd frontend-mobile && npm start` and press `i` (iOS) or `a`
   (Android).
3. Tap **Sign in with Google** (or Facebook). The in-app browser opens
   on the provider's consent screen.
4. Approve. The browser hands the token back to the app via the
   `threadloop://` deep-link scheme (registered in `app.json`).
5. The app posts to `/api/auth/{provider}/callback`. On success the
   stack switches to `MeScreen` with your display name + email.
6. Kill the app and relaunch. Silent refresh restores the session —
   you should land on `MeScreen` directly without re-authenticating.

The `link_required` flow is exercised by signing in with a second
provider whose email matches an existing first-provider account. The
modal appears, you re-auth with the original provider, and on success
both identities are merged onto the same `users` row.

## Web

The mobile workspace can render via `expo start --web` for quick
component sanity-checks. The canonical web client is
`frontend-web/`; treat the mobile web target as a development
convenience, not a deployable surface. `expo-auth-session` works on
web but the redirect URI registration is different from the mobile
schemes — for real web sign-in use `frontend-web/`.

## What this app does NOT do (yet)

- Apple sign-in. Descoped to Epic #57 per RFC 0001 § "Deferred providers".
- Push notifications, deep links beyond the auth-session redirect,
  OTA updates — these are tracked under their own future Epics.
- AR try-on. Tracked under the AR viewer Epic.
- Listing browsing / search / transactions UI. None of these are
  built yet on any client.
