import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import { AppHeader } from "./AppHeader";

// Wire is camelCase per ADR 0009 — keys mirror what the backend serializes.
const wireSession = {
  linkRequired: false,
  accessToken: "access-jwt",
  expiresAt: "2030-01-01T00:00:00Z",
  user: {
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
  },
};

describe("AppHeader", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the signed-in user's display name when authenticated", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(wireSession), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    render(
      <MemoryRouter>
        <AuthProvider>
          <AppHeader />
        </AuthProvider>
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByTestId("app-header-display-name").textContent).toBe("Ada Lovelace");
    });
    expect(screen.getByTestId("app-header-user").dataset.authStatus).toBe("authenticated");
  });

  it("shows a Sign in link when anonymous", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 401 }));
    render(
      <MemoryRouter>
        <AuthProvider>
          <AppHeader />
        </AuthProvider>
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByRole("link", { name: /sign in/i })).toBeInTheDocument();
    });
    expect(screen.getByTestId("app-header-user").dataset.authStatus).toBe("anonymous");
  });
});
