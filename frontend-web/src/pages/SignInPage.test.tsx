import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import type { GoogleCredentialResponse, GoogleIdApi } from "../auth/google";
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

const wireUser = {
  id: "00000000-0000-0000-0000-000000000001",
  provider: "google",
  email: "ada@example.com",
  email_verified: true,
  display_name: "Ada Lovelace",
  avatar_url: null,
  can_sell: false,
  can_purchase: true,
  seller_rating: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const wireSession = {
  link_required: false,
  access_token: "access-jwt",
  expires_at: "2030-01-01T00:00:00Z",
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
  });

  afterEach(() => {
    vi.restoreAllMocks();
    delete window.__threadloopGoogleIdStub__;
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

  it("renders the link_required generic error without redirecting", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = typeof input === "string" ? input : input instanceof Request ? input.url : input.toString();
      if (url.includes("/api/auth/refresh")) {
        return Promise.resolve(new Response(null, { status: 401 }));
      }
      if (url.includes("/api/auth/google/callback")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              link_required: true,
              link_provider: "apple",
              link_token: "link-jwt",
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
