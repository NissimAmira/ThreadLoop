/**
 * Sign in with Apple JS loader + thin typed wrapper.
 *
 * The Apple JS SDK is fetched from
 * `https://appleid.cdn-apple.com/appleauth/static/jsapi/appleid/1/en_US/appleid.auth.js`.
 * It exposes `window.AppleID.auth`, which we narrow here so the sign-in page
 * doesn't carry the typing burden.
 *
 * Cypress / unit tests don't load real Apple JS — they install
 * `window.__threadloopAppleIdStub__` before the page mounts, and the loader
 * returns that stub instead of injecting a `<script>`. This keeps the smoke
 * test deterministic without needing a real Apple Service ID.
 *
 * Slice 2 (#38) — mirrors the structure of `google.ts`.
 */

/**
 * `AppleID.auth.signIn()` resolves with this envelope.
 *
 * `user` is only populated on the very first sign-in for a given Apple ID
 * (and only when the app requested the `name` scope). Subsequent sign-ins
 * omit it; the backend reuses the existing `users.display_name`.
 */
export interface AppleSignInResponse {
  authorization: {
    /** ID token (JWT). What the backend verifies against Apple's JWKS. */
    id_token: string;
    /** Authorization code. Required by the OpenAPI contract; not exchanged in slice 2. */
    code: string;
    /** Opaque state value the SDK echoes back; we don't use it. */
    state?: string;
  };
  /** Present only on first authentication when `name` scope was requested. */
  user?: {
    name?: {
      firstName?: string;
      lastName?: string;
    };
    email?: string;
  };
}

interface AppleAuthInitConfig {
  clientId: string;
  scope: string;
  redirectURI: string;
  state?: string;
  nonce?: string;
  usePopup: boolean;
}

export interface AppleIdAuthApi {
  init: (config: AppleAuthInitConfig) => void;
  signIn: () => Promise<AppleSignInResponse>;
}

declare global {
  interface Window {
    AppleID?: {
      auth?: AppleIdAuthApi;
    };
    /**
     * Test-only override. When set before the sign-in page mounts, the loader
     * returns this value instead of injecting the real Apple JS script.
     * Cypress uses it to stub the sign-in promise deterministically.
     */
    __threadloopAppleIdStub__?: AppleIdAuthApi;
  }
}

const APPLE_JS_SRC =
  "https://appleid.cdn-apple.com/appleauth/static/jsapi/appleid/1/en_US/appleid.auth.js";

let loaderPromise: Promise<AppleIdAuthApi> | null = null;

export function loadAppleIdentity(): Promise<AppleIdAuthApi> {
  if (typeof window === "undefined") {
    return Promise.reject(new Error("Apple Identity is browser-only"));
  }
  if (window.__threadloopAppleIdStub__) {
    return Promise.resolve(window.__threadloopAppleIdStub__);
  }
  if (window.AppleID?.auth) {
    return Promise.resolve(window.AppleID.auth);
  }
  if (loaderPromise) return loaderPromise;

  loaderPromise = new Promise<AppleIdAuthApi>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = APPLE_JS_SRC;
    script.async = true;
    script.defer = true;

    script.addEventListener(
      "load",
      () => {
        const api = window.AppleID?.auth;
        if (!api) {
          reject(
            new Error(
              "Apple Identity script loaded but window.AppleID.auth is missing",
            ),
          );
          return;
        }
        resolve(api);
      },
      { once: true },
    );
    script.addEventListener(
      "error",
      () => {
        loaderPromise = null;
        reject(new Error("Failed to load Sign in with Apple JS"));
      },
      { once: true },
    );

    document.head.appendChild(script);
  });

  return loaderPromise;
}

/**
 * Compose the optional `name` field for `POST /api/auth/apple/callback`.
 * Apple ships first/last on first sign-in only; the backend treats a missing
 * value as "use existing display_name or fall back to email." Returns
 * `undefined` when nothing useful is present so we don't post `{ name: "" }`.
 */
export function composeAppleDisplayName(
  user: AppleSignInResponse["user"],
): string | undefined {
  const first = user?.name?.firstName?.trim() ?? "";
  const last = user?.name?.lastName?.trim() ?? "";
  const joined = [first, last].filter(Boolean).join(" ");
  return joined.length > 0 ? joined : undefined;
}
