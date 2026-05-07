/**
 * Facebook Login SDK loader + thin typed wrapper.
 *
 * The Facebook JS SDK is fetched from
 * `https://connect.facebook.net/en_US/sdk.js`. It exposes `window.FB` once
 * loaded; the SDK additionally calls `window.fbAsyncInit` synchronously
 * during script execution, but since we install the SDK after page mount
 * we just poll for `window.FB` after `load`.
 *
 * Cypress / unit tests don't load the real SDK — they install
 * `window.__threadloopFacebookIdStub__` before the page mounts, and the
 * loader returns that stub instead of injecting a `<script>`. This keeps
 * the smoke test deterministic without needing a real Facebook App ID.
 *
 * Slice 3 (#39) — mirrors the structure of `google.ts` and `apple.ts`.
 */

/**
 * `FB.login()` resolves with this envelope. The fields we care about live
 * under `authResponse`; the SDK itself emits a wider shape that we ignore.
 *
 * `status === "connected"` is the success case. Any other value (`"unknown"`,
 * `"not_authorized"`, etc.) means the user closed the popup or denied the
 * login — `authResponse` is then `null`.
 */
export interface FacebookLoginResponse {
  status: "connected" | "not_authorized" | "unknown";
  authResponse: {
    accessToken: string;
    userID: string;
    expiresIn?: number;
    signedRequest?: string;
  } | null;
}

interface FacebookInitConfig {
  appId: string;
  version: string;
  cookie?: boolean;
  xfbml?: boolean;
}

interface FacebookLoginOptions {
  scope?: string;
  return_scopes?: boolean;
}

export interface FacebookSdkApi {
  init: (config: FacebookInitConfig) => void;
  login: (
    callback: (response: FacebookLoginResponse) => void,
    options?: FacebookLoginOptions,
  ) => void;
  getLoginStatus?: (
    callback: (response: FacebookLoginResponse) => void,
  ) => void;
}

declare global {
  interface Window {
    FB?: FacebookSdkApi;
    /**
     * The official SDK calls this synchronously during script execution.
     * We don't rely on it (we resolve the loader on `<script>` `load`
     * after the SDK has finished its init dance), but typing it keeps
     * future re-introduction painless.
     */
    fbAsyncInit?: () => void;
    /**
     * Test-only override. When set before the sign-in page mounts, the
     * loader returns this value instead of injecting the real Facebook
     * SDK script. Cypress uses it to stub `FB.login` deterministically.
     */
    __threadloopFacebookIdStub__?: FacebookSdkApi;
  }
}

const FB_SDK_SRC = "https://connect.facebook.net/en_US/sdk.js";

let loaderPromise: Promise<FacebookSdkApi> | null = null;

export function loadFacebookIdentity(): Promise<FacebookSdkApi> {
  if (typeof window === "undefined") {
    return Promise.reject(new Error("Facebook Identity is browser-only"));
  }
  if (window.__threadloopFacebookIdStub__) {
    return Promise.resolve(window.__threadloopFacebookIdStub__);
  }
  if (window.FB) {
    return Promise.resolve(window.FB);
  }
  if (loaderPromise) return loaderPromise;

  loaderPromise = new Promise<FacebookSdkApi>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = FB_SDK_SRC;
    script.async = true;
    script.defer = true;
    script.crossOrigin = "anonymous";

    script.addEventListener(
      "load",
      () => {
        const api = window.FB;
        if (!api) {
          reject(
            new Error(
              "Facebook SDK script loaded but window.FB is missing",
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
        reject(new Error("Failed to load Facebook Login SDK"));
      },
      { once: true },
    );

    document.head.appendChild(script);
  });

  return loaderPromise;
}
