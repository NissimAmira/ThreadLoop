import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import { MePage } from "./MePage";

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

describe("MePage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the user's display name and email when authenticated", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(wireSession), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    render(
      <MemoryRouter initialEntries={["/me"]}>
        <AuthProvider>
          <Routes>
            <Route path="/me" element={<MePage />} />
            <Route path="/sign-in" element={<p>sign-in route</p>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByTestId("me-display-name").textContent).toBe("Ada Lovelace");
    });
    expect(screen.getByTestId("me-email").textContent).toBe("ada@example.com");
  });

  it("redirects anonymous visitors to /sign-in?next=/me", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 401 }));
    render(
      <MemoryRouter initialEntries={["/me"]}>
        <AuthProvider>
          <Routes>
            <Route path="/me" element={<MePage />} />
            <Route path="/sign-in" element={<p>sign-in route</p>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByText("sign-in route")).toBeInTheDocument();
    });
  });
});
