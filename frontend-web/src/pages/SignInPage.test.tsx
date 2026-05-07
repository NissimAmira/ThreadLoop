import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import type { GoogleCredentialResponse, GoogleIdApi } from "../auth/google";
import * as googleModule from "../auth/google";
import type { AppleIdAuthApi, AppleSignInResponse } from "../auth/apple";
import * as appleModule from "../auth/apple";
import type { FacebookLoginResponse, FacebookSdkApi } from "../auth/facebook";
import * as facebookModule from "../auth/facebook";
import { SignInPage, safeNext } from "./SignInPage";
import { MePage } from "./MePage";

interface StubHandle {
  api: GoogleIdApi;
  fireCredential: (resp: GoogleCredentialResponse) => void;
  buttonRendered: () => boolean;
}

function installGisStub(): StubHandle {
  let callback: ((resp: GoogleCredentialResponse) => void) | null = null;
  let rendered = false;
  const stub: GoogleIdApi = {
    initialize: (config) => {
      callback = config.callback;
    },
    renderButton: (parent) => {
      rendered = true;
      const btn = document.createElement("button");
      btn.textContent = "Sign in with Google";
      btn.type = "button";
      parent.appendChild(btn);
    },
    prompt: () => {},
    cancel: () => {},
    disableAutoSelect: () => {},
  };
  window.__threadloopGoogleIdStub__ = stub;
  return {
    api: stub,
    fireCredential: (resp) => {
      if (!callback) throw new Error("Google callback not yet registered");
      callback(resp);
    },
    buttonRendered: () => rendered,
  };
}

interface AppleStubHandle {
  api: AppleIdAuthApi;
  setNextResponse: (
    resp: AppleSignInResponse | { error: string } | Error,
  ) => void;
  initCalled: () => boolean;
}

function installAppleStub(): AppleStubHandle {
  let nextResponse: AppleSignInResponse | { error: string } | Error = {
    authorization: { id_token: "stub-id-token", code: "stub-code" },
  };
  let initCalled = false;
  const stub: AppleIdAuthApi = {
    init: () => {
      initCalled = true;
    },
    signIn: () => {
      if (nextResponse instanceof Error) return Promise.reject(nextResponse);
      if ("error" in nextResponse) return Promise.reject(nextResponse);
      return Promise.resolve(nextResponse);
    },
  };
  window.__threadloopAppleIdStub__ = stub;
  return {
    api: stub,
    setNextResponse: (resp) => {
      nextResponse = resp;
    },
    initCalled: () => initCalled,
  };
}

interface FacebookStubHandle {
  api: FacebookSdkApi;
  setNextResponse: (resp: FacebookLoginResponse) => void;
  initCalled: () => boolean;
}

function installFacebookStub(): FacebookStubHandle {
  let nextResponse: FacebookLoginResponse = {
    status: "connected",
    authResponse: { accessToken: "stub-fb-token", userID: "1" },
  };
  let initCalled = false;
  const stub: FacebookSdkApi = {
    init: () => {
      initCalled = true;
    },
    login: (cb) => cb(nextResponse),
  };
  window.__threadloopFacebookIdStub__ = stub;
  return {
    api: stub,
    setNextResponse: (resp) => {
      nextResponse = resp;
    },
    initCalled: () => initCalled,
  };
}

// Wire is camelCase per ADR 0009 — keys mirror what the backend serializes.
const wireUser = {
  id: "00000000-0000-0000-0000-000000000001",
  provider: "google",
  email: "ada@example.com",
  emailVerified: true,
  displayName: "Ada Lovelace",
  avatarUrl: null,
  canSell: false,
  canPurchase: true,
  sellerRating: null,
  createdAt: "2026-01-01T00:00:00Z",
  updatedAt: "2026-01-01T00:00:00Z",
};

const wireSession = {
  linkRequired: false,
  accessToken: "access-jwt",
  expiresAt: "2030-01-01T00:00:00Z",
  user: wireUser,
};

function renderSignIn(initialPath = "/sign-in") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <AuthProvider>
        <Routes>
          <Route path="/sign-in" element={<SignInPage />} />
          <Route path="/me" element={<MePage />} />
          <Route path="/" element={<p>home</p>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("SignInPage", () => {
  beforeEach(() => {
    delete window.__threadloopGoogleIdStub__;
    delete window.__threadloopAppleIdStub__;
    delete window.__threadloopFacebookIdStub__;
    // Default test bench mirrors a build with all three providers wired up
    // (slice 3 / #39 shipped). Individual tests override these when they
    // specifically exercise a flag-flip path.
    vi.stubEnv("VITE_GOOGLE_ENABLED", "true");
    vi.stubEnv("VITE_APPLE_ENABLED", "true");
    vi.stubEnv("VITE_FACEBOOK_ENABLED", "true");
    vi.stubEnv("VITE_FACEBOOK_APP_ID", "stub-fb-app-id");
    // Default Facebook stub keeps tests that don't exercise the FB flow
    // from blowing up trying to fetch the real SDK. Tests that *do*
    // exercise FB call `installFacebookStub()` which overwrites this.
    window.__threadloopFacebookIdStub__ = {
      init: () => {},
      login: () => {},
    };
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    delete window.__threadloopGoogleIdStub__;
    delete window.__threadloopAppleIdStub__;
    delete window.__threadloopFacebookIdStub__;
  });

  it("renders a Google button via the GIS stub once anonymous", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 401 }),
    );
    const stub = installGisStub();
    renderSignIn();
    await waitFor(() => expect(stub.buttonRendered()).toBe(true));
    expect(screen.getByLabelText("Sign in with Google")).toBeInTheDocument();
  });

  it("redirects to ?next on a successful credential exchange", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    fetchMock.mockImplementation((input) => {
      const url = typeof input === "string" ? input : input instanceof Request ? input.url : input.toString();
      if (url.includes("/api/auth/refresh")) {
        return Promise.resolve(new Response(null, { status: 401 }));
      }
      if (url.includes("/api/auth/google/callback")) {
        return Promise.resolve(
          new Response(JSON.stringify(wireSession), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
      return Promise.reject(new Error(`unexpected fetch: ${url}`));
    });
    const stub = installGisStub();
    renderSignIn("/sign-in?next=/me");
    await waitFor(() => expect(stub.buttonRendered()).toBe(true));

    await act(async () => {
      stub.fireCredential({ credential: "id-token-from-google" });
    });

    await waitFor(() => {
      expect(screen.getByTestId("me-display-name").textContent).toBe("Ada Lovelace");
    });
  });

  it("renders the linkRequired generic error without redirecting", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = typeof input === "string" ? input : input instanceof Request ? input.url : input.toString();
      if (url.includes("/api/auth/refresh")) {
        return Promise.resolve(new Response(null, { status: 401 }));
      }
      if (url.includes("/api/auth/google/callback")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              linkRequired: true,
              linkProvider: "apple",
              linkToken: "link-jwt",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
      }
      return Promise.reject(new Error(`unexpected fetch: ${url}`));
    });
    const stub = installGisStub();
    renderSignIn();
    await waitFor(() => expect(stub.buttonRendered()).toBe(true));

    await act(async () => {
      stub.fireCredential({ credential: "id-token-from-google" });
    });

    await waitFor(() => {
      expect(screen.getByTestId("sign-in-error").textContent).toMatch(
        /registered with another provider/i,
      );
    });
  });

  it("renders a retryable error on a 401 from the callback", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = typeof input === "string" ? input : input instanceof Request ? input.url : input.toString();
      if (url.includes("/api/auth/refresh")) {
        return Promise.resolve(new Response(null, { status: 401 }));
      }
      if (url.includes("/api/auth/google/callback")) {
        return Promise.resolve(
          new Response(JSON.stringify({ code: "invalid_token", message: "bad" }), {
            status: 401,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
      return Promise.reject(new Error(`unexpected fetch: ${url}`));
    });
    const stub = installGisStub();
    renderSignIn();
    await waitFor(() => expect(stub.buttonRendered()).toBe(true));

    await act(async () => {
      stub.fireCredential({ credential: "bad-token" });
    });

    await waitFor(() => {
      expect(screen.getByTestId("sign-in-error").textContent).toMatch(/rejected/i);
    });
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("renders the Apple button enabled once init() resolves", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 401 }),
    );
    installGisStub();
    installAppleStub();
    renderSignIn();
    const button = await screen.findByTestId("apple-signin-button");
    expect(button).toBeInTheDocument();
    expect(button).toHaveAttribute("aria-label", "Sign in with Apple");
    // Initially disabled while the SDK init() promise resolves; flips to
    // enabled below.
    await waitFor(() => expect(button).not.toBeDisabled());
  });

  it("renders the Apple button disabled with an actionable error when VITE_APPLE_CLIENT_ID is unset", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 401 }),
    );
    installGisStub();
    // Deliberately do NOT install the Apple stub — the loader resolves to
    // a no-op API surface so we can drive the !APPLE_CLIENT_ID branch.
    const noopApi: AppleIdAuthApi = {
      init: () => {},
      signIn: () =>
        Promise.reject(new Error("signIn should not be called in this path")),
    };
    vi.spyOn(appleModule, "loadAppleIdentity").mockResolvedValue(noopApi);
    renderSignIn();
    const button = await screen.findByTestId("apple-signin-button");
    expect(button).toBeInTheDocument();
    // VITE_APPLE_CLIENT_ID is unset in test env and no stub is installed,
    // so the button stays disabled and an actionable error is shown.
    await waitFor(() => {
      expect(screen.getByTestId("sign-in-error").textContent).toMatch(
        /Apple sign-in is not configured for this build/i,
      );
    });
    expect(button).toBeDisabled();
  });

  it("renders the Apple button disabled with a retryable error when the SDK script fails to load", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 401 }),
    );
    installGisStub();
    vi.spyOn(appleModule, "loadAppleIdentity").mockRejectedValue(
      new Error("Failed to load Sign in with Apple JS"),
    );
    renderSignIn();
    const button = await screen.findByTestId("apple-signin-button");
    await waitFor(() => {
      expect(screen.getByTestId("sign-in-error").textContent).toMatch(
        /Could not load Apple sign-in/i,
      );
    });
    expect(button).toBeDisabled();
  });

  it("Apple flow → posts idToken+code+name and redirects to ?next", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    fetchMock.mockImplementation((input) => {
      const url = typeof input === "string" ? input : input instanceof Request ? input.url : input.toString();
      if (url.includes("/api/auth/refresh")) {
        return Promise.resolve(new Response(null, { status: 401 }));
      }
      if (url.includes("/api/auth/apple/callback")) {
        return Promise.resolve(
          new Response(JSON.stringify(wireSession), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
      return Promise.reject(new Error(`unexpected fetch: ${url}`));
    });
    installGisStub();
    const apple = installAppleStub();
    apple.setNextResponse({
      authorization: { id_token: "apple-id-token", code: "apple-code" },
      user: { name: { firstName: "Ada", lastName: "Lovelace" } },
    });
    renderSignIn("/sign-in?next=/me");

    const btn = await screen.findByTestId("apple-signin-button");
    await waitFor(() => expect(btn).not.toBeDisabled());

    await act(async () => {
      fireEvent.click(btn);
    });

    await waitFor(() => {
      expect(screen.getByTestId("me-display-name").textContent).toBe("Ada Lovelace");
    });

    const appleCall = fetchMock.mock.calls.find((c) =>
      typeof c[0] === "string" ? c[0].includes("/api/auth/apple/callback") : false,
    );
    expect(appleCall).toBeDefined();
    const init = appleCall![1] as RequestInit;
    expect(init.body).toBe(
      JSON.stringify({
        idToken: "apple-id-token",
        code: "apple-code",
        name: "Ada Lovelace",
      }),
    );
  });

  it("Apple linkRequired surfaces the generic cross-provider error", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = typeof input === "string" ? input : input instanceof Request ? input.url : input.toString();
      if (url.includes("/api/auth/refresh")) {
        return Promise.resolve(new Response(null, { status: 401 }));
      }
      if (url.includes("/api/auth/apple/callback")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              linkRequired: true,
              linkProvider: "google",
              linkToken: "link-jwt",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
      }
      return Promise.reject(new Error(`unexpected fetch: ${url}`));
    });
    installGisStub();
    const apple = installAppleStub();
    apple.setNextResponse({
      authorization: { id_token: "apple-id-token", code: "apple-code" },
    });
    renderSignIn();

    const btn = await screen.findByTestId("apple-signin-button");
    await waitFor(() => expect(btn).not.toBeDisabled());

    await act(async () => {
      fireEvent.click(btn);
    });

    await waitFor(() => {
      expect(screen.getByTestId("sign-in-error").textContent).toMatch(
        /registered with another provider/i,
      );
    });
  });

  it("Apple-relay-email accounts (privaterelay.appleid.com) sign in cleanly", async () => {
    const relayUser = {
      ...wireUser,
      provider: "apple",
      email: "abc123xyz@privaterelay.appleid.com",
      emailVerified: true,
    };
    const relaySession = { ...wireSession, user: relayUser };
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = typeof input === "string" ? input : input instanceof Request ? input.url : input.toString();
      if (url.includes("/api/auth/refresh")) {
        return Promise.resolve(new Response(null, { status: 401 }));
      }
      if (url.includes("/api/auth/apple/callback")) {
        return Promise.resolve(
          new Response(JSON.stringify(relaySession), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
      return Promise.reject(new Error(`unexpected fetch: ${url}`));
    });
    installGisStub();
    const apple = installAppleStub();
    apple.setNextResponse({
      authorization: { id_token: "apple-id-token", code: "apple-code" },
    });
    renderSignIn("/sign-in?next=/me");

    const btn = await screen.findByTestId("apple-signin-button");
    await waitFor(() => expect(btn).not.toBeDisabled());

    await act(async () => {
      fireEvent.click(btn);
    });

    await waitFor(() => {
      expect(screen.getByTestId("me-email").textContent).toBe(
        "abc123xyz@privaterelay.appleid.com",
      );
    });
  });

  it("Apple SDK rejection surfaces a retryable error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 401 }),
    );
    installGisStub();
    const apple = installAppleStub();
    apple.setNextResponse(new Error("network down"));
    renderSignIn();

    const btn = await screen.findByTestId("apple-signin-button");
    await waitFor(() => expect(btn).not.toBeDisabled());

    await act(async () => {
      fireEvent.click(btn);
    });

    await waitFor(() => {
      expect(screen.getByTestId("sign-in-error").textContent).toMatch(
        /Apple sign-in/i,
      );
    });
  });

  it("hides the Apple button entirely (no dev-flavoured error) when VITE_APPLE_CLIENT_ID is unset in non-DEV mode", async () => {
    vi.stubEnv("DEV", false);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 401 }),
    );
    installGisStub();
    const noopApi: AppleIdAuthApi = {
      init: () => {},
      signIn: () =>
        Promise.reject(new Error("signIn should not be called in this path")),
    };
    vi.spyOn(appleModule, "loadAppleIdentity").mockResolvedValue(noopApi);
    renderSignIn();

    // Google still renders (its stub is installed) so the page settles.
    await waitFor(() =>
      expect(screen.getByLabelText("Sign in with Google")).toBeInTheDocument(),
    );
    // The dev-flavoured error must NOT appear in prod-mode.
    expect(screen.getByTestId("sign-in-error").textContent ?? "").not.toMatch(
      /VITE_APPLE_CLIENT_ID/i,
    );
    expect(screen.getByTestId("sign-in-error").textContent ?? "").not.toMatch(
      /not configured for this build/i,
    );
    // The Apple button itself is removed from the tree.
    await waitFor(() =>
      expect(screen.queryByTestId("apple-signin-button")).toBeNull(),
    );
    vi.unstubAllEnvs();
  });

  it("hides the Google button entirely (no dev-flavoured error) when VITE_GOOGLE_CLIENT_ID is unset in non-DEV mode", async () => {
    vi.stubEnv("DEV", false);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 401 }),
    );
    // Deliberately do NOT install the Google stub — the loader resolves
    // to the (test-injected) global, so we must override at module scope.
    // We mock the loader to return a no-op API that should never be invoked.
    const noopGis: GoogleIdApi = {
      initialize: () => {},
      renderButton: () => {
        throw new Error("renderButton should not be called when hidden");
      },
      prompt: () => {},
      cancel: () => {},
      disableAutoSelect: () => {},
    };
    vi.spyOn(googleModule, "loadGoogleIdentity").mockResolvedValue(noopGis);
    installAppleStub();
    renderSignIn();

    // Apple still renders (its stub is installed) so the page settles.
    const appleBtn = await screen.findByTestId("apple-signin-button");
    await waitFor(() => expect(appleBtn).not.toBeDisabled());
    // The dev-flavoured error must NOT appear in prod-mode.
    expect(screen.getByTestId("sign-in-error").textContent ?? "").not.toMatch(
      /VITE_GOOGLE_CLIENT_ID/i,
    );
    expect(screen.getByTestId("sign-in-error").textContent ?? "").not.toMatch(
      /not configured for this build/i,
    );
    // The Google container itself is removed from the tree.
    expect(screen.queryByTestId("google-button-container")).toBeNull();
    vi.unstubAllEnvs();
  });

  it("renders an empty-state message when every provider's VITE_*_ENABLED flag is false", async () => {
    // The new trigger for the empty state is "all three per-provider flags
    // are off", not "every client ID happens to be unset". This is the
    // scalable signal: a deploy that explicitly disables every provider
    // (e.g. an all-providers-misconfigured staging) gets the empty state
    // regardless of whether stale client IDs linger in the env.
    vi.stubEnv("VITE_GOOGLE_ENABLED", "false");
    vi.stubEnv("VITE_APPLE_ENABLED", "false");
    vi.stubEnv("VITE_FACEBOOK_ENABLED", "false");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 401 }),
    );
    renderSignIn();

    // The empty-state message renders.
    await screen.findByTestId("sign-in-unavailable");
    expect(screen.getByTestId("sign-in-unavailable").textContent).toMatch(
      /Sign-in is currently unavailable/i,
    );
    // No button is in the tree (Facebook has no button anyway, Google and
    // Apple short-circuit on the flag).
    expect(screen.queryByTestId("apple-signin-button")).toBeNull();
    expect(screen.queryByTestId("google-button-container")).toBeNull();
    // No dev-flavoured error leaks.
    expect(screen.getByTestId("sign-in-error").textContent ?? "").not.toMatch(
      /not configured for this build/i,
    );
  });

  it("hides the Apple button when VITE_APPLE_ENABLED=false (flag wins over a set client ID)", async () => {
    // The flag is the explicit "is this provider live in this build" signal
    // and must win even when a (stale or otherwise) VITE_APPLE_CLIENT_ID is
    // present. Stubbing both directions here documents the precedence.
    vi.stubEnv("VITE_APPLE_ENABLED", "false");
    vi.stubEnv("VITE_APPLE_CLIENT_ID", "stub-apple-service-id");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 401 }),
    );
    installGisStub();
    // If the flag-gate works, the Apple loader should NEVER be called —
    // a flag-disabled provider has no business fetching its SDK script.
    const loaderSpy = vi.spyOn(appleModule, "loadAppleIdentity");
    renderSignIn();

    // Google still settles so the page renders.
    await waitFor(() =>
      expect(screen.getByLabelText("Sign in with Google")).toBeInTheDocument(),
    );
    // Apple button absent.
    expect(screen.queryByTestId("apple-signin-button")).toBeNull();
    // No dev error leaked either.
    expect(screen.getByTestId("sign-in-error").textContent ?? "").not.toMatch(
      /Apple sign-in is not configured for this build/i,
    );
    // SDK loader was never invoked — flag short-circuit fires before
    // any provider-side work.
    expect(loaderSpy).not.toHaveBeenCalled();
  });

  it("hides the Google button when VITE_GOOGLE_ENABLED=false (flag wins over a set client ID)", async () => {
    vi.stubEnv("VITE_GOOGLE_ENABLED", "false");
    vi.stubEnv("VITE_GOOGLE_CLIENT_ID", "stub-google-client-id");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 401 }),
    );
    installAppleStub();
    const loaderSpy = vi.spyOn(googleModule, "loadGoogleIdentity");
    renderSignIn();

    // Apple still settles so the page renders.
    const appleBtn = await screen.findByTestId("apple-signin-button");
    await waitFor(() => expect(appleBtn).not.toBeDisabled());
    // Google container absent.
    expect(screen.queryByTestId("google-button-container")).toBeNull();
    expect(screen.getByTestId("sign-in-error").textContent ?? "").not.toMatch(
      /Google sign-in is not configured for this build/i,
    );
    expect(loaderSpy).not.toHaveBeenCalled();
  });

  it("VITE_APPLE_ENABLED=true + missing client ID + DEV mode still surfaces the actionable developer error", async () => {
    // Loud-misconfiguration semantics for an *active* provider must stay
    // intact. The new flag layer doesn't suppress the existing DEV-mode
    // error path — it only adds a higher-priority "flag is off" gate above
    // it. This test covers the ENABLED=true + DEV path.
    vi.stubEnv("VITE_APPLE_ENABLED", "true");
    vi.stubEnv("DEV", true);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 401 }),
    );
    installGisStub();
    const noopApi: AppleIdAuthApi = {
      init: () => {},
      signIn: () =>
        Promise.reject(new Error("signIn should not be called in this path")),
    };
    vi.spyOn(appleModule, "loadAppleIdentity").mockResolvedValue(noopApi);
    renderSignIn();

    const button = await screen.findByTestId("apple-signin-button");
    expect(button).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId("sign-in-error").textContent).toMatch(
        /Apple sign-in is not configured for this build/i,
      );
    });
    expect(button).toBeDisabled();
  });

  it("Apple user-cancel does not surface a scary error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 401 }),
    );
    installGisStub();
    const apple = installAppleStub();
    apple.setNextResponse({ error: "popup_closed_by_user" });
    renderSignIn();

    const btn = await screen.findByTestId("apple-signin-button");
    await waitFor(() => expect(btn).not.toBeDisabled());

    await act(async () => {
      fireEvent.click(btn);
    });

    // Give the click handler a chance to settle.
    await waitFor(() => expect(btn).not.toBeDisabled());
    expect(screen.getByTestId("sign-in-error").textContent ?? "").toBe("");
  });

  // ---- Facebook (slice 3 / #39) ----

  it("renders the Facebook button enabled once init() resolves", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 401 }),
    );
    installGisStub();
    installAppleStub();
    installFacebookStub();
    renderSignIn();
    const button = await screen.findByTestId("facebook-signin-button");
    expect(button).toBeInTheDocument();
    expect(button).toHaveAttribute("aria-label", "Sign in with Facebook");
    await waitFor(() => expect(button).not.toBeDisabled());
  });

  it("Facebook flow → posts accessToken and redirects to ?next", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    fetchMock.mockImplementation((input) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof Request
            ? input.url
            : input.toString();
      if (url.includes("/api/auth/refresh")) {
        return Promise.resolve(new Response(null, { status: 401 }));
      }
      if (url.includes("/api/auth/facebook/callback")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              linkRequired: false,
              accessToken: "access-jwt",
              expiresAt: "2030-01-01T00:00:00Z",
              user: { ...wireUser, provider: "facebook", emailVerified: false },
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
      }
      return Promise.reject(new Error(`unexpected fetch: ${url}`));
    });
    installGisStub();
    installAppleStub();
    const fb = installFacebookStub();
    fb.setNextResponse({
      status: "connected",
      authResponse: { accessToken: "fb-user-token", userID: "42" },
    });
    renderSignIn("/sign-in?next=/me");

    const btn = await screen.findByTestId("facebook-signin-button");
    await waitFor(() => expect(btn).not.toBeDisabled());

    await act(async () => {
      fireEvent.click(btn);
    });

    await waitFor(() => {
      expect(screen.getByTestId("me-display-name").textContent).toBe(
        "Ada Lovelace",
      );
    });

    const fbCall = fetchMock.mock.calls.find((c) =>
      typeof c[0] === "string"
        ? c[0].includes("/api/auth/facebook/callback")
        : false,
    );
    expect(fbCall).toBeDefined();
    const init = fbCall![1] as RequestInit;
    expect(init.body).toBe(JSON.stringify({ accessToken: "fb-user-token" }));
  });

  it("Facebook email-permission-decline (BE returns email=null) signs in cleanly", async () => {
    const noEmailUser = {
      ...wireUser,
      provider: "facebook",
      email: null,
      emailVerified: false,
    };
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof Request
            ? input.url
            : input.toString();
      if (url.includes("/api/auth/refresh")) {
        return Promise.resolve(new Response(null, { status: 401 }));
      }
      if (url.includes("/api/auth/facebook/callback")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              linkRequired: false,
              accessToken: "access-jwt",
              expiresAt: "2030-01-01T00:00:00Z",
              user: noEmailUser,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
      }
      return Promise.reject(new Error(`unexpected fetch: ${url}`));
    });
    installGisStub();
    installAppleStub();
    const fb = installFacebookStub();
    fb.setNextResponse({
      status: "connected",
      authResponse: { accessToken: "fb-user-token", userID: "42" },
    });
    renderSignIn("/sign-in?next=/me");

    const btn = await screen.findByTestId("facebook-signin-button");
    await waitFor(() => expect(btn).not.toBeDisabled());

    await act(async () => {
      fireEvent.click(btn);
    });

    // Lands on /me without erroring (display_name fallback covers the
    // missing email — BE keeps display_name even when email is null).
    await waitFor(() => {
      expect(screen.getByTestId("me-display-name").textContent).toBe(
        "Ada Lovelace",
      );
    });
  });

  it("Facebook 401 surfaces the rejected-token retry message", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof Request
            ? input.url
            : input.toString();
      if (url.includes("/api/auth/refresh")) {
        return Promise.resolve(new Response(null, { status: 401 }));
      }
      if (url.includes("/api/auth/facebook/callback")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({ code: "invalid_token", message: "bad" }),
            { status: 401, headers: { "Content-Type": "application/json" } },
          ),
        );
      }
      return Promise.reject(new Error(`unexpected fetch: ${url}`));
    });
    installGisStub();
    installAppleStub();
    const fb = installFacebookStub();
    fb.setNextResponse({
      status: "connected",
      authResponse: { accessToken: "fb-user-token", userID: "42" },
    });
    renderSignIn();

    const btn = await screen.findByTestId("facebook-signin-button");
    await waitFor(() => expect(btn).not.toBeDisabled());

    await act(async () => {
      fireEvent.click(btn);
    });

    await waitFor(() => {
      expect(screen.getByTestId("sign-in-error").textContent).toMatch(
        /Facebook sign-in was rejected/i,
      );
    });
  });

  it("Facebook 503 surfaces the FB-specific 'in a few minutes' copy", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof Request
            ? input.url
            : input.toString();
      if (url.includes("/api/auth/refresh")) {
        return Promise.resolve(new Response(null, { status: 401 }));
      }
      if (url.includes("/api/auth/facebook/callback")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              code: "graph_api_unavailable",
              message: "down",
            }),
            { status: 503, headers: { "Content-Type": "application/json" } },
          ),
        );
      }
      return Promise.reject(new Error(`unexpected fetch: ${url}`));
    });
    installGisStub();
    installAppleStub();
    const fb = installFacebookStub();
    fb.setNextResponse({
      status: "connected",
      authResponse: { accessToken: "fb-user-token", userID: "42" },
    });
    renderSignIn();

    const btn = await screen.findByTestId("facebook-signin-button");
    await waitFor(() => expect(btn).not.toBeDisabled());

    await act(async () => {
      fireEvent.click(btn);
    });

    // FB-specific copy: "in a few minutes" (vs. Google/Apple which omit it).
    await waitFor(() => {
      expect(screen.getByTestId("sign-in-error").textContent).toMatch(
        /Facebook sign-in is temporarily unavailable\. Please try again in a few minutes/i,
      );
    });
  });

  it("Facebook user-cancel (status != 'connected') does not surface a scary error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 401 }),
    );
    installGisStub();
    installAppleStub();
    const fb = installFacebookStub();
    fb.setNextResponse({ status: "unknown", authResponse: null });
    renderSignIn();

    const btn = await screen.findByTestId("facebook-signin-button");
    await waitFor(() => expect(btn).not.toBeDisabled());

    await act(async () => {
      fireEvent.click(btn);
    });

    await waitFor(() => expect(btn).not.toBeDisabled());
    expect(screen.getByTestId("sign-in-error").textContent ?? "").toBe("");
  });

  it("hides the Facebook button when VITE_FACEBOOK_ENABLED=false (flag wins over a set app id)", async () => {
    vi.stubEnv("VITE_FACEBOOK_ENABLED", "false");
    vi.stubEnv("VITE_FACEBOOK_APP_ID", "stub-fb-app-id");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 401 }),
    );
    installGisStub();
    installAppleStub();
    // If the flag-gate works, the Facebook loader should NEVER be called.
    const loaderSpy = vi.spyOn(facebookModule, "loadFacebookIdentity");
    renderSignIn();

    // Other providers settle so the page renders.
    await waitFor(() =>
      expect(screen.getByLabelText("Sign in with Google")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("facebook-signin-button")).toBeNull();
    expect(screen.getByTestId("sign-in-error").textContent ?? "").not.toMatch(
      /Facebook sign-in is not configured for this build/i,
    );
    expect(loaderSpy).not.toHaveBeenCalled();
  });

  it("VITE_FACEBOOK_ENABLED=true + missing app id + DEV mode surfaces the actionable developer error", async () => {
    vi.stubEnv("VITE_FACEBOOK_ENABLED", "true");
    vi.stubEnv("VITE_FACEBOOK_APP_ID", "");
    vi.stubEnv("DEV", true);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 401 }),
    );
    installGisStub();
    installAppleStub();
    // Don't install the FB stub — drive the missing-app-id branch via a
    // loader that resolves to a no-op API surface.
    delete window.__threadloopFacebookIdStub__;
    const noopApi: FacebookSdkApi = {
      init: () => {},
      login: () => {
        throw new Error("login should not be called in this path");
      },
    };
    vi.spyOn(facebookModule, "loadFacebookIdentity").mockResolvedValue(noopApi);
    renderSignIn();

    const button = await screen.findByTestId("facebook-signin-button");
    expect(button).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId("sign-in-error").textContent).toMatch(
        /Facebook sign-in is not configured for this build/i,
      );
    });
    expect(button).toBeDisabled();
  });

  it("hides the Facebook button entirely (no dev error) when app id is unset in non-DEV mode", async () => {
    vi.stubEnv("VITE_FACEBOOK_APP_ID", "");
    vi.stubEnv("DEV", false);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 401 }),
    );
    installGisStub();
    installAppleStub();
    delete window.__threadloopFacebookIdStub__;
    const noopApi: FacebookSdkApi = {
      init: () => {},
      login: () => {
        throw new Error("login should not be called in this path");
      },
    };
    vi.spyOn(facebookModule, "loadFacebookIdentity").mockResolvedValue(noopApi);
    renderSignIn();

    await waitFor(() =>
      expect(screen.getByLabelText("Sign in with Google")).toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(screen.queryByTestId("facebook-signin-button")).toBeNull(),
    );
    expect(screen.getByTestId("sign-in-error").textContent ?? "").not.toMatch(
      /not configured for this build/i,
    );
    vi.unstubAllEnvs();
  });
});

describe("safeNext", () => {
  it("accepts a same-origin app path", () => {
    expect(safeNext("/me")).toBe("/me");
    expect(safeNext("/listings/abc?x=1")).toBe("/listings/abc?x=1");
  });

  it("rejects protocol-relative URLs", () => {
    expect(safeNext("//evil.example.com/path")).toBe("/");
    expect(safeNext("//evil")).toBe("/");
  });

  it("rejects javascript: URIs", () => {
    expect(safeNext("javascript:alert(1)")).toBe("/");
  });

  it("rejects absolute URLs", () => {
    expect(safeNext("http://evil")).toBe("/");
    expect(safeNext("https://evil.example.com/me")).toBe("/");
  });

  it("rejects backslash-trick URLs", () => {
    expect(safeNext("/\\evil.example.com")).toBe("/");
  });

  it("falls back to / for empty / null", () => {
    expect(safeNext(null)).toBe("/");
    expect(safeNext("")).toBe("/");
  });
});
