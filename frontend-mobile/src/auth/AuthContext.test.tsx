import { act, render, waitFor } from "@testing-library/react-native";
import { Text } from "react-native";
import type { AuthenticatedSession, Session, User } from "@threadloop/shared";
import { ApiError, api } from "../api/client";
import { AuthProvider, useAuth } from "./AuthContext";
import * as secureStore from "./secureStore";

jest.mock("../api/client", () => {
  const actual = jest.requireActual("../api/client");
  return {
    ...actual,
    api: {
      health: jest.fn(),
      auth: {
        googleCallback: jest.fn(),
        facebookCallback: jest.fn(),
        link: jest.fn(),
        refresh: jest.fn(),
        logout: jest.fn(),
      },
      me: jest.fn(),
    },
  };
});

const TEST_USER: User = {
  id: "00000000-0000-0000-0000-000000000001",
  provider: "google",
  email: "alice@example.com",
  emailVerified: true,
  displayName: "Alice",
  avatarUrl: null,
  canSell: false,
  canPurchase: true,
  sellerRating: null,
  createdAt: "2026-01-01T00:00:00Z",
  updatedAt: "2026-01-01T00:00:00Z",
};

const AUTHENTICATED_SESSION: AuthenticatedSession = {
  linkRequired: false,
  accessToken: "test-access-token",
  expiresAt: "2026-01-01T00:15:00Z",
  user: TEST_USER,
};

function Probe({
  onReady,
}: {
  onReady: (ctx: ReturnType<typeof useAuth>) => void;
}) {
  const ctx = useAuth();
  onReady(ctx);
  return <Text testID="probe-status">{ctx.state.status}</Text>;
}

// `jest.Mocked` doesn't deep-mock nested objects, so reach in by hand.
// All members of `api.auth` are `jest.fn()` per the mock factory above.
const apiMock = api as unknown as {
  auth: {
    googleCallback: jest.Mock;
    facebookCallback: jest.Mock;
    link: jest.Mock;
    refresh: jest.Mock;
    logout: jest.Mock;
  };
  me: jest.Mock;
  health: jest.Mock;
};

describe("AuthContext", () => {
  beforeEach(async () => {
    jest.clearAllMocks();
    await secureStore.clearAccessToken();
  });

  it("hydrates to authenticated when /api/auth/refresh returns a session", async () => {
    apiMock.auth.refresh.mockResolvedValueOnce(AUTHENTICATED_SESSION);

    const ctxRef: { current: ReturnType<typeof useAuth> | null } = { current: null };
    const { getByTestId } = render(
      <AuthProvider>
        <Probe onReady={(ctx) => { ctxRef.current = ctx; }} />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(getByTestId("probe-status").props.children).toBe("authenticated");
    });

    expect(ctxRef.current).not.toBeNull();
    if (ctxRef.current && ctxRef.current.state.status === "authenticated") {
      expect(ctxRef.current.state.user.displayName).toBe("Alice");
      expect(ctxRef.current.state.accessToken).toBe("test-access-token");
    }

    const stored = await secureStore.getAccessToken();
    expect(stored).toBe("test-access-token");
  });

  it("hydrates to anonymous on 401 refresh and clears any stored access token", async () => {
    await secureStore.setAccessToken("stale-token");
    apiMock.auth.refresh.mockRejectedValueOnce(
      new ApiError(401, "invalid_refresh_token"),
    );

    const { getByTestId } = render(
      <AuthProvider>
        <Probe onReady={() => undefined} />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(getByTestId("probe-status").props.children).toBe("anonymous");
    });

    expect(await secureStore.getAccessToken()).toBeNull();
  });

  it("hydrates to anonymous when refresh returns a link-required envelope (defensive)", async () => {
    const pending: Session = {
      linkRequired: true,
      linkProvider: "google",
      linkToken: "link-token",
    };
    apiMock.auth.refresh.mockResolvedValueOnce(pending);

    const { getByTestId } = render(
      <AuthProvider>
        <Probe onReady={() => undefined} />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(getByTestId("probe-status").props.children).toBe("anonymous");
    });
  });

  it("falls back to stored access token + /api/me on network failure", async () => {
    await secureStore.setAccessToken("cached-token");
    apiMock.auth.refresh.mockRejectedValueOnce(new TypeError("Network down"));
    apiMock.me.mockResolvedValueOnce(TEST_USER);

    const ctxRef: { current: ReturnType<typeof useAuth> | null } = { current: null };
    const { getByTestId } = render(
      <AuthProvider>
        <Probe onReady={(ctx) => { ctxRef.current = ctx; }} />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(getByTestId("probe-status").props.children).toBe("authenticated");
    });
    expect(apiMock.me).toHaveBeenCalledWith("cached-token");
    // Degraded hydrate path sets `offline=true` so screens can render
    // the "Working offline" banner.
    expect(ctxRef.current?.offline).toBe(true);
  });

  it("falls back to cached token on a 5xx from /api/auth/refresh (not just network reject)", async () => {
    // Regression test: previously any `ApiError` (including 5xx)
    // signed the user out. Per `docs/auth.md` § "Network-failure
    // fallback", only a genuine 401 should drop the token; 5xx is a
    // transport / availability failure and should hit the same
    // cached-token + /api/me degraded path as a network reject.
    await secureStore.setAccessToken("cached-token");
    apiMock.auth.refresh.mockRejectedValueOnce(
      new ApiError(503, "service_unavailable"),
    );
    apiMock.me.mockResolvedValueOnce(TEST_USER);

    const ctxRef: { current: ReturnType<typeof useAuth> | null } = { current: null };
    const { getByTestId } = render(
      <AuthProvider>
        <Probe onReady={(ctx) => { ctxRef.current = ctx; }} />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(getByTestId("probe-status").props.children).toBe("authenticated");
    });
    expect(apiMock.me).toHaveBeenCalledWith("cached-token");
    // Cached token must NOT be cleared on a transient 5xx.
    expect(await secureStore.getAccessToken()).toBe("cached-token");
    expect(ctxRef.current?.offline).toBe(true);
  });

  it("signIn promotes a fresh callback session into authenticated state", async () => {
    apiMock.auth.refresh.mockRejectedValueOnce(
      new ApiError(401, "invalid_refresh_token"),
    );

    const ctxRef: { current: ReturnType<typeof useAuth> | null } = { current: null };
    const { getByTestId } = render(
      <AuthProvider>
        <Probe onReady={(ctx) => { ctxRef.current = ctx; }} />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(getByTestId("probe-status").props.children).toBe("anonymous");
    });

    expect(ctxRef.current).not.toBeNull();
    act(() => {
      ctxRef.current!.signIn(AUTHENTICATED_SESSION);
    });

    await waitFor(() => {
      expect(getByTestId("probe-status").props.children).toBe("authenticated");
    });

    await waitFor(async () => {
      expect(await secureStore.getAccessToken()).toBe("test-access-token");
    });
  });

  it("signOut posts /api/auth/logout, clears the stored token, and returns to anonymous", async () => {
    apiMock.auth.refresh.mockResolvedValueOnce(AUTHENTICATED_SESSION);
    apiMock.auth.logout.mockResolvedValueOnce(undefined);

    const ctxRef: { current: ReturnType<typeof useAuth> | null } = { current: null };
    const { getByTestId } = render(
      <AuthProvider>
        <Probe onReady={(ctx) => { ctxRef.current = ctx; }} />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(getByTestId("probe-status").props.children).toBe("authenticated");
    });

    await act(async () => {
      await ctxRef.current!.signOut();
    });

    expect(apiMock.auth.logout).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(getByTestId("probe-status").props.children).toBe("anonymous");
    });
    expect(await secureStore.getAccessToken()).toBeNull();
  });

  it("signOut still drops state even when /api/auth/logout fails", async () => {
    apiMock.auth.refresh.mockResolvedValueOnce(AUTHENTICATED_SESSION);
    apiMock.auth.logout.mockRejectedValueOnce(new TypeError("Network down"));

    const ctxRef: { current: ReturnType<typeof useAuth> | null } = { current: null };
    const { getByTestId } = render(
      <AuthProvider>
        <Probe onReady={(ctx) => { ctxRef.current = ctx; }} />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(getByTestId("probe-status").props.children).toBe("authenticated");
    });

    await act(async () => {
      await ctxRef.current!.signOut();
    });

    await waitFor(() => {
      expect(getByTestId("probe-status").props.children).toBe("anonymous");
    });
    expect(await secureStore.getAccessToken()).toBeNull();
  });
});
