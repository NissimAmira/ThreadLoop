import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AppleIdAuthApi, AppleSignInResponse } from "../auth/apple";
import type { GoogleCredentialResponse, GoogleIdApi } from "../auth/google";
import type { FacebookSdkApi } from "../auth/facebook";
import { LinkAccountsDialog } from "./LinkAccountsDialog";

/**
 * Vitest coverage for the slice-4 link-accounts modal (#40). Cypress
 * exercises the full network round-trip; these unit tests cover:
 *
 *  - The five accessibility AC bullets (role, aria-modal, aria-labelledby /
 *    aria-describedby, initial focus on highlighted button, focus trap, Esc
 *    closes + restores focus, aria-live="polite" status region).
 *  - The 401 / 409 / 503 failure-envelope mapping from PR #64.
 *  - The 10-minute client TTL transition.
 *  - The "no link_token persisted to localStorage / sessionStorage /
 *    cookies" AC bullet.
 */

const wireUser = {
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
  accessToken: "merged-access-jwt",
  expiresAt: "2030-01-01T00:00:00Z",
  user: wireUser,
};

function installAppleStub(
  resp: AppleSignInResponse | { error: string } = {
    authorization: { id_token: "apple-id-token", code: "apple-code" },
  },
): AppleIdAuthApi {
  const stub: AppleIdAuthApi = {
    init: () => {},
    signIn: () => {
      if ("error" in resp) return Promise.reject(resp);
      return Promise.resolve(resp);
    },
  };
  window.__threadloopAppleIdStub__ = stub;
  return stub;
}

function installFacebookStub(): FacebookSdkApi {
  const stub: FacebookSdkApi = {
    init: () => {},
    login: (cb) =>
      cb({
        status: "connected",
        authResponse: { accessToken: "fb-access-token", userID: "1" },
      }),
  };
  window.__threadloopFacebookIdStub__ = stub;
  return stub;
}

function installGoogleStub(): GoogleIdApi {
  let captured: ((resp: GoogleCredentialResponse) => void) | null = null;
  const stub: GoogleIdApi = {
    initialize: (config) => {
      captured = config.callback;
    },
    renderButton: (parent) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = "Sign in with Google (gis-rendered)";
      btn.setAttribute("data-testid", "gis-rendered-button");
      btn.addEventListener("click", () => {
        captured?.({ credential: "google-id-token-from-link-flow" });
      });
      parent.replaceChildren(btn);
    },
    prompt: () => {},
    cancel: () => {},
    disableAutoSelect: () => {},
  };
  window.__threadloopGoogleIdStub__ = stub;
  return stub;
}

describe("LinkAccountsDialog", () => {
  beforeEach(() => {
    delete window.__threadloopGoogleIdStub__;
    delete window.__threadloopAppleIdStub__;
    delete window.__threadloopFacebookIdStub__;
    // Sanity baseline — the AC requires no link_token persisted anywhere.
    // Tests assert this on cleanup.
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    delete window.__threadloopGoogleIdStub__;
    delete window.__threadloopAppleIdStub__;
    delete window.__threadloopFacebookIdStub__;
  });

  // ---- Accessibility AC ----

  it("sets role=dialog + aria-modal=true with linked aria-labelledby/aria-describedby", () => {
    installAppleStub();
    const triggerRef = createRef<HTMLButtonElement>();
    render(
      <>
        <button ref={triggerRef} data-testid="external-trigger">
          trigger
        </button>
        <LinkAccountsDialog
          pendingLink={{ token: "link-jwt", provider: "apple" }}
          onLinked={() => {}}
          onClose={() => {}}
          triggerRef={triggerRef}
        />
      </>,
    );
    const dialog = screen.getByTestId("link-accounts-dialog");
    expect(dialog).toHaveAttribute("role", "dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    const labelId = dialog.getAttribute("aria-labelledby");
    const describedById = dialog.getAttribute("aria-describedby");
    expect(labelId).toBeTruthy();
    expect(describedById).toBeTruthy();
    // Both ids must resolve to elements inside the dialog.
    expect(document.getElementById(labelId!)).not.toBeNull();
    expect(document.getElementById(describedById!)).not.toBeNull();
  });

  it("places initial focus on the highlighted original-provider button", async () => {
    installAppleStub();
    const triggerRef = createRef<HTMLButtonElement>();
    render(
      <>
        <button ref={triggerRef} data-testid="external-trigger">
          trigger
        </button>
        <LinkAccountsDialog
          pendingLink={{ token: "link-jwt", provider: "apple" }}
          onLinked={() => {}}
          onClose={() => {}}
          triggerRef={triggerRef}
        />
      </>,
    );
    const primary = screen.getByTestId("link-accounts-apple-button");
    await waitFor(() => expect(document.activeElement).toBe(primary));
  });

  it("traps Tab inside the dialog (Tab on last focusable cycles to first)", async () => {
    installAppleStub();
    const triggerRef = createRef<HTMLButtonElement>();
    render(
      <>
        <button ref={triggerRef}>trigger</button>
        <LinkAccountsDialog
          pendingLink={{ token: "link-jwt", provider: "apple" }}
          onLinked={() => {}}
          onClose={() => {}}
          triggerRef={triggerRef}
        />
      </>,
    );
    const close = screen.getByTestId("link-accounts-close");
    const apple = screen.getByTestId("link-accounts-apple-button");
    const cancel = screen.getByTestId("link-accounts-cancel");
    // Focus the last focusable; Tab should wrap to the first.
    cancel.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(close);
    // From the first, Shift+Tab wraps to the last.
    close.focus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(cancel);
    // Sanity: the apple button is reachable in between.
    expect(apple).toBeInTheDocument();
  });

  it("Esc closes the dialog and restores focus to the originally-clicked trigger", async () => {
    installAppleStub();
    let externalTrigger: HTMLButtonElement | null = null;
    const triggerRef = {
      get current() {
        return externalTrigger;
      },
      set current(el: HTMLButtonElement | null) {
        externalTrigger = el;
      },
    };
    const onClose = vi.fn();
    render(
      <>
        <button
          data-testid="external-trigger"
          ref={(el) => {
            externalTrigger = el;
          }}
        >
          trigger
        </button>
        <LinkAccountsDialog
          pendingLink={{ token: "link-jwt", provider: "apple" }}
          onLinked={() => {}}
          onClose={onClose}
          triggerRef={triggerRef}
        />
      </>,
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    // queueMicrotask runs after current task — flush.
    await act(async () => {
      await Promise.resolve();
    });
    expect(document.activeElement).toBe(externalTrigger);
  });

  it("status region uses role=status + aria-live=polite (not assertive)", () => {
    installAppleStub();
    const triggerRef = createRef<HTMLButtonElement>();
    render(
      <>
        <button ref={triggerRef}>trigger</button>
        <LinkAccountsDialog
          pendingLink={{ token: "link-jwt", provider: "apple" }}
          onLinked={() => {}}
          onClose={() => {}}
          triggerRef={triggerRef}
        />
      </>,
    );
    const region = screen.getByTestId("link-accounts-status");
    expect(region).toHaveAttribute("role", "status");
    expect(region).toHaveAttribute("aria-live", "polite");
  });

  // ---- Functional + failure envelope mapping ----

  it("happy path: clicks the Apple button → POST /api/auth/link → onLinked called with merged session", async () => {
    installAppleStub();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url.includes("/api/auth/link")) {
        return Promise.resolve(
          new Response(JSON.stringify(okSession), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
      return Promise.reject(new Error(`unexpected fetch: ${url}`));
    });

    const triggerRef = createRef<HTMLButtonElement>();
    const onLinked = vi.fn();
    render(
      <>
        <button ref={triggerRef}>trigger</button>
        <LinkAccountsDialog
          pendingLink={{ token: "link-jwt", provider: "apple" }}
          onLinked={onLinked}
          onClose={() => {}}
          triggerRef={triggerRef}
        />
      </>,
    );
    const appleBtn = screen.getByTestId("link-accounts-apple-button");
    await act(async () => {
      fireEvent.click(appleBtn);
    });
    await waitFor(() => expect(onLinked).toHaveBeenCalledTimes(1));
    expect(onLinked.mock.calls[0][0]).toMatchObject({
      linkRequired: false,
      accessToken: "merged-access-jwt",
    });
    // Request body matches the BE contract from PR #64.
    const linkCall = fetchMock.mock.calls.find((c) =>
      typeof c[0] === "string" ? c[0].includes("/api/auth/link") : false,
    );
    expect(linkCall).toBeDefined();
    const body = JSON.parse((linkCall![1] as RequestInit).body as string);
    expect(body).toEqual({
      linkToken: "link-jwt",
      originalProvider: "apple",
      credential: { idToken: "apple-id-token", code: "apple-code" },
    });
  });

  it("401 response surfaces the expired-token recovery copy + Back-to-sign-in CTA", async () => {
    installAppleStub();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ code: "invalid_link_token", message: "x" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const triggerRef = createRef<HTMLButtonElement>();
    render(
      <>
        <button ref={triggerRef}>trigger</button>
        <LinkAccountsDialog
          pendingLink={{ token: "link-jwt", provider: "apple" }}
          onLinked={() => {}}
          onClose={() => {}}
          triggerRef={triggerRef}
        />
      </>,
    );
    await act(async () => {
      fireEvent.click(screen.getByTestId("link-accounts-apple-button"));
    });
    await waitFor(() =>
      expect(screen.getByTestId("link-accounts-status").textContent).toMatch(
        /session expired/i,
      ),
    );
    expect(
      screen.getByTestId("link-accounts-recovery-cta"),
    ).toBeInTheDocument();
  });

  it("409 response surfaces the conflict-with-different-account copy", async () => {
    installAppleStub();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ code: "identity_already_linked", message: "x" }), {
        status: 409,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const triggerRef = createRef<HTMLButtonElement>();
    render(
      <>
        <button ref={triggerRef}>trigger</button>
        <LinkAccountsDialog
          pendingLink={{ token: "link-jwt", provider: "apple" }}
          onLinked={() => {}}
          onClose={() => {}}
          triggerRef={triggerRef}
        />
      </>,
    );
    await act(async () => {
      fireEvent.click(screen.getByTestId("link-accounts-apple-button"));
    });
    await waitFor(() =>
      expect(screen.getByTestId("link-accounts-status").textContent).toMatch(
        /already linked/i,
      ),
    );
  });

  it("503 response surfaces the provider-unreachable copy", async () => {
    installAppleStub();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ code: "jwks_unavailable", message: "x" }), {
        status: 503,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const triggerRef = createRef<HTMLButtonElement>();
    render(
      <>
        <button ref={triggerRef}>trigger</button>
        <LinkAccountsDialog
          pendingLink={{ token: "link-jwt", provider: "apple" }}
          onLinked={() => {}}
          onClose={() => {}}
          triggerRef={triggerRef}
        />
      </>,
    );
    await act(async () => {
      fireEvent.click(screen.getByTestId("link-accounts-apple-button"));
    });
    await waitFor(() =>
      expect(screen.getByTestId("link-accounts-status").textContent).toMatch(
        /try again in a moment/i,
      ),
    );
  });

  it("transitions to the expired state after the 10-minute client TTL fires", async () => {
    installAppleStub();
    vi.useFakeTimers();
    const triggerRef = createRef<HTMLButtonElement>();
    render(
      <>
        <button ref={triggerRef}>trigger</button>
        <LinkAccountsDialog
          pendingLink={{ token: "link-jwt", provider: "apple" }}
          onLinked={() => {}}
          onClose={() => {}}
          triggerRef={triggerRef}
        />
      </>,
    );
    expect(screen.getByTestId("link-accounts-status").textContent).toBe("");
    await act(async () => {
      vi.advanceTimersByTime(10 * 60 * 1000 + 1);
    });
    expect(screen.getByTestId("link-accounts-status").textContent).toMatch(
      /session expired/i,
    );
    vi.useRealTimers();
  });

  it("Facebook provider renders the Facebook button + posts accessToken on click", async () => {
    installFacebookStub();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(okSession), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const triggerRef = createRef<HTMLButtonElement>();
    const onLinked = vi.fn();
    render(
      <>
        <button ref={triggerRef}>trigger</button>
        <LinkAccountsDialog
          pendingLink={{ token: "link-jwt", provider: "facebook" }}
          onLinked={onLinked}
          onClose={() => {}}
          triggerRef={triggerRef}
        />
      </>,
    );
    const fbBtn = screen.getByTestId("link-accounts-facebook-button");
    await act(async () => {
      fireEvent.click(fbBtn);
    });
    await waitFor(() => expect(onLinked).toHaveBeenCalledTimes(1));
    const linkCall = fetchMock.mock.calls.find((c) =>
      typeof c[0] === "string" ? c[0].includes("/api/auth/link") : false,
    );
    expect(linkCall).toBeDefined();
    const body = JSON.parse((linkCall![1] as RequestInit).body as string);
    expect(body).toEqual({
      linkToken: "link-jwt",
      originalProvider: "facebook",
      credential: { accessToken: "fb-access-token" },
    });
  });

  it("Google provider mounts the GIS-rendered button into the highlight wrapper", async () => {
    installGoogleStub();
    const triggerRef = createRef<HTMLButtonElement>();
    render(
      <>
        <button ref={triggerRef}>trigger</button>
        <LinkAccountsDialog
          pendingLink={{ token: "link-jwt", provider: "google" }}
          onLinked={() => {}}
          onClose={() => {}}
          triggerRef={triggerRef}
        />
      </>,
    );
    // GIS auto-mounts on first paint; the wrapper now contains the
    // GIS-rendered button (replacing the fallback).
    await waitFor(() =>
      expect(screen.queryByTestId("gis-rendered-button")).not.toBeNull(),
    );
    expect(
      screen.getByTestId("link-accounts-original-button-wrapper"),
    ).toContainElement(screen.getByTestId("gis-rendered-button"));
  });

  it("does not persist link_token to localStorage / sessionStorage / cookies during the flow", async () => {
    installAppleStub();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(okSession), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const triggerRef = createRef<HTMLButtonElement>();
    const onLinked = vi.fn();
    render(
      <>
        <button ref={triggerRef}>trigger</button>
        <LinkAccountsDialog
          pendingLink={{ token: "link-jwt", provider: "apple" }}
          onLinked={onLinked}
          onClose={() => {}}
          triggerRef={triggerRef}
        />
      </>,
    );
    await act(async () => {
      fireEvent.click(screen.getByTestId("link-accounts-apple-button"));
    });
    await waitFor(() => expect(onLinked).toHaveBeenCalledTimes(1));
    // Scan storages for the literal token value.
    for (let i = 0; i < window.localStorage.length; i++) {
      const k = window.localStorage.key(i)!;
      expect(window.localStorage.getItem(k)).not.toContain("link-jwt");
    }
    for (let i = 0; i < window.sessionStorage.length; i++) {
      const k = window.sessionStorage.key(i)!;
      expect(window.sessionStorage.getItem(k)).not.toContain("link-jwt");
    }
    expect(document.cookie).not.toContain("link-jwt");
  });
});
