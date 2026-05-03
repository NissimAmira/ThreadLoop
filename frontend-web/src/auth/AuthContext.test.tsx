import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "./AuthContext";

// Wire is camelCase per ADR 0009 — keys mirror what the backend serializes.
function makeWireSession(displayName = "Ada Lovelace") {
  return {
    linkRequired: false,
    accessToken: "access-jwt",
    expiresAt: "2030-01-01T00:00:00Z",
    user: {
      id: "00000000-0000-0000-0000-000000000001",
      provider: "google",
      email: "ada@example.com",
      emailVerified: true,
      displayName: displayName,
      avatarUrl: null,
      canSell: false,
      canPurchase: true,
      sellerRating: null,
      createdAt: "2026-01-01T00:00:00Z",
      updatedAt: "2026-01-01T00:00:00Z",
    },
  };
}

function Probe() {
  const { state, signOut } = useAuth();
  if (state.status !== "authenticated") return <p>state:{state.status}</p>;
  return (
    <div>
      <p>state:{state.status}</p>
      <p data-testid="probe-name">{state.user.displayName}</p>
      <p data-testid="probe-token">{state.accessToken}</p>
      <button type="button" onClick={() => void signOut()}>logout</button>
    </div>
  );
}

describe("AuthProvider", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("hydrates as authenticated when /api/auth/refresh succeeds", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(makeWireSession()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId("probe-name").textContent).toBe("Ada Lovelace");
    });
    expect(screen.getByTestId("probe-token").textContent).toBe("access-jwt");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/auth/refresh"),
      expect.objectContaining({ method: "POST", credentials: "include" }),
    );
  });

  it("falls back to anonymous when refresh returns 401", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ code: "invalid_refresh_token", message: "no" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      }),
    );
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText("state:anonymous")).toBeInTheDocument();
    });
  });

  it("falls back to anonymous when refresh network errors", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("offline"));
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText("state:anonymous")).toBeInTheDocument();
    });
  });

  it("signOut posts to /api/auth/logout and drops to anonymous even on failure", async () => {
    const refreshResp = new Response(JSON.stringify(makeWireSession()), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementationOnce(() => Promise.resolve(refreshResp))
      .mockImplementationOnce(() => Promise.reject(new Error("logout failed")));

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText("state:authenticated")).toBeInTheDocument();
    });

    await act(async () => {
      screen.getByText("logout").click();
    });

    await waitFor(() => {
      expect(screen.getByText("state:anonymous")).toBeInTheDocument();
    });
    expect(fetchMock).toHaveBeenLastCalledWith(
      expect.stringContaining("/api/auth/logout"),
      expect.objectContaining({ method: "POST" }),
    );
  });
});
