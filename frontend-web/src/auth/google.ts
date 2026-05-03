/**
 * Google Identity Services (GIS) loader + thin typed wrapper.
 *
 * The GIS script is fetched from `https://accounts.google.com/gsi/client`. It
 * exposes `window.google.accounts.id` once loaded, which we narrow here so the
 * sign-in page doesn't carry the typing burden.
 *
 * Cypress / unit tests don't load real GIS — they install
 * `window.__threadloopGoogleIdStub__` before the page mounts, and the
 * loader returns that stub instead of injecting a `<script>`. This keeps the
 * E2E smoke deterministic without needing a real Google client id.
 */

export interface GoogleCredentialResponse {
  credential: string;
  select_by?: string;
}

interface GoogleIdConfig {
  client_id: string;
  callback: (response: GoogleCredentialResponse) => void;
  auto_select?: boolean;
  ux_mode?: "popup" | "redirect";
}

interface GoogleIdButtonConfig {
  type?: "standard" | "icon";
  theme?: "outline" | "filled_blue" | "filled_black";
  size?: "large" | "medium" | "small";
  text?: "signin_with" | "signup_with" | "continue_with" | "signin";
  shape?: "rectangular" | "pill" | "circle" | "square";
  width?: number | string;
  logo_alignment?: "left" | "center";
}

export interface GoogleIdApi {
  initialize: (config: GoogleIdConfig) => void;
  renderButton: (parent: HTMLElement, options: GoogleIdButtonConfig) => void;
  prompt: () => void;
  cancel: () => void;
  disableAutoSelect: () => void;
}

declare global {
  interface Window {
    google?: {
      accounts?: {
        id?: GoogleIdApi;
      };
    };
    /**
     * Test-only override. When set before the sign-in page mounts, the loader
     * returns this value instead of injecting the real GIS script. Cypress
     * uses it to stub the credential callback deterministically.
     */
    __threadloopGoogleIdStub__?: GoogleIdApi;
  }
}

const GIS_SRC = "https://accounts.google.com/gsi/client";

let loaderPromise: Promise<GoogleIdApi> | null = null;

export function loadGoogleIdentity(): Promise<GoogleIdApi> {
  if (typeof window === "undefined") {
    return Promise.reject(new Error("Google Identity is browser-only"));
  }
  if (window.__threadloopGoogleIdStub__) {
    return Promise.resolve(window.__threadloopGoogleIdStub__);
  }
  if (window.google?.accounts?.id) {
    return Promise.resolve(window.google.accounts.id);
  }
  if (loaderPromise) return loaderPromise;

  loaderPromise = new Promise<GoogleIdApi>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = GIS_SRC;
    script.async = true;
    script.defer = true;

    script.addEventListener(
      "load",
      () => {
        const api = window.google?.accounts?.id;
        if (!api) {
          reject(new Error("Google Identity Services script loaded but window.google.accounts.id is missing"));
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
        reject(new Error("Failed to load Google Identity Services"));
      },
      { once: true },
    );

    document.head.appendChild(script);
  });

  return loaderPromise;
}
