import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "./client";

// Wire is camelCase on every property per ADR 0009. The fixtures below match
// what the backend actually serializes — no per-endpoint adapter to convert.

const okUser = {
  id: "00000000-0000-0000-0000-000000000001",
  provider: "google" as const,
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

const okSession = {
  linkRequired: false,
  accessToken: "access-jwt",
  expiresAt: "2030-01-01T00:00:00Z",
  user: okUser,
};

describe("api client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns the camelCase Session shape from the wire directly", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(okSession), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const session = await api.auth.googleCallback("id-token-from-google");
    expect(session.linkRequired).toBe(false);
    if (session.linkRequired) throw new Error("unreachable");
    expect(session.accessToken).toBe("access-jwt");
    expect(session.user.displayName).toBe("Ada Lovelace");
    expect(session.user.emailVerified).toBe(true);
    expect(session.user.canPurchase).toBe(true);
  });

  it("posts the request body in camelCase (idToken, not id_token)", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(okSession), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await api.auth.googleCallback("id-token-from-google");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.body).toBe(JSON.stringify({ idToken: "id-token-from-google" }));
  });

  it("surfaces the linkRequired pending-link branch", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          linkRequired: true,
          linkProvider: "apple",
          linkToken: "link-jwt",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    const session = await api.auth.googleCallback("id-token-from-google");
    expect(session.linkRequired).toBe(true);
    if (!session.linkRequired) throw new Error("unreachable");
    expect(session.linkProvider).toBe("apple");
    expect(session.linkToken).toBe("link-jwt");
  });

  it("throws ApiError with the OpenAPI error code on a 4xx", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ code: "invalid_token", message: "rejected" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await expect(api.auth.googleCallback("bad-token")).rejects.toMatchObject({
      name: "ApiError",
      status: 401,
      code: "invalid_token",
    });
  });

  it("populates ApiError.requestId from the camelCase error envelope", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ code: "invalid_token", message: "x", requestId: "req-abc" }),
        { status: 401, headers: { "Content-Type": "application/json" } },
      ),
    );
    await expect(api.auth.googleCallback("bad-token")).rejects.toMatchObject({
      name: "ApiError",
      requestId: "req-abc",
    });
  });

  it("attaches the bearer token on /api/me", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(okUser), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const user = await api.me("access-jwt");
    expect(user.displayName).toBe("Ada Lovelace");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer access-jwt");
  });

  it("returns void for 204 logout", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 204 }));
    await expect(api.auth.logout()).resolves.toBeUndefined();
  });

  it("ApiError exposes status and code", () => {
    const e = new ApiError(503, "down", "jwks_unavailable");
    expect(e.status).toBe(503);
    expect(e.code).toBe("jwks_unavailable");
    expect(e.name).toBe("ApiError");
  });
});
