import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import type { GoogleCredentialResponse, GoogleIdApi } from "../auth/google";
import * as googleModule from "../auth/google";
import type { AppleIdAuthApi, AppleSignInResponse } from "../auth/apple";
import * as appleModule from "../auth/apple";
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
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    delete window.__threadloopGoogleIdStub__;
    delete window.__threadloopAppleIdStub__;
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
