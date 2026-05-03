import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "./client";

const okWireUser = {
  id: "00000000-0000-0000-0000-000000000001",
  provider: "google" as const,
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

const okWireSession = {
  link_required: false,
  access_token: "access-jwt",
  expires_at: "2030-01-01T00:00:00Z",
  user: okWireUser,
};

describe("api client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("converts the snake_case Session wire shape to the camelCase TS shape", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(okWireSession), {
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

  it("surfaces the link_required pending-link branch", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          link_required: true,
          link_provider: "apple",
          link_token: "link-jwt",
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

  it("attaches the bearer token on /api/me", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(okWireUser), {
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
