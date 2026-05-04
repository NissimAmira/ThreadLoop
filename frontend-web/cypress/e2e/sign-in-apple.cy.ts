/**
 * Slice-2 Apple sign-in smoke test (#38).
 *
 * Stubs the Sign in with Apple JS SDK by injecting
 * `window.__threadloopAppleIdStub__` before the page loads, so the page
 * never reaches the real `appleid.cdn-apple.com/...`. The stub captures the
 * config from `init()` and returns a fake `AppleSignInResponse` from
 * `signIn()`. We click the FE-rendered Apple button, the page exchanges the
 * (fake) response against an intercepted `POST /api/auth/apple/callback`,
 * lands on `/me`, and we assert the user is shown.
 *
 * Mirrors `sign-in.cy.ts` (Google smoke) — Apple's flow lives in the same
 * page; we keep the spec separate so each provider's smoke can run / fail
 * independently.
 */

import type { AppleIdAuthApi, AppleSignInResponse } from "../../src/auth/apple";
import type { GoogleIdApi } from "../../src/auth/google";

// Wire is camelCase per ADR 0009 — keys here mirror what the backend
// actually serializes (no FE adapter to translate snake_case anymore).
const wireUser = {
  id: "00000000-0000-0000-0000-000000000002",
  provider: "apple",
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
  accessToken: "stub-access-jwt",
  expiresAt: "2030-01-01T00:00:00Z",
  user: wireUser,
};

const wireRelaySession = {
  ...wireSession,
  user: {
    ...wireUser,
    email: "abc123xyz@privaterelay.appleid.com",
  },
};

function installStubs(
  win: Cypress.AUTWindow,
  appleResponse: AppleSignInResponse,
) {
  // Inert Google stub — the page mounts both SDKs on /sign-in and we want
  // the Google init not to throw. The credential callback is never fired.
  const googleStub: GoogleIdApi = {
    initialize: () => {},
    renderButton: (parent) => {
      const btn = win.document.createElement("button");
      btn.type = "button";
      btn.textContent = "Sign in with Google (stub)";
      btn.setAttribute("data-cy", "google-signin-stub-button");
      parent.appendChild(btn);
    },
    prompt: () => {},
    cancel: () => {},
    disableAutoSelect: () => {},
  };
  (win as Window).__threadloopGoogleIdStub__ = googleStub;

  const appleStub: AppleIdAuthApi = {
    init: () => {},
    signIn: () => Promise.resolve(appleResponse),
  };
  (win as Window).__threadloopAppleIdStub__ = appleStub;
}

describe("ThreadLoop sign-in (slice 2: Apple)", () => {
  beforeEach(() => {
    cy.intercept("POST", "/api/auth/refresh", {
      statusCode: 401,
      body: { code: "no", message: "no" },
    }).as("refresh");
    cy.intercept("GET", "/api/health", {
      statusCode: 200,
      body: { status: "ok", version: "0.1.0", db: "ok", redis: "ok", meili: "ok" },
    });
  });

  it("Apple flow → lands signed in on /me", () => {
    cy.intercept("POST", "/api/auth/apple/callback", {
      statusCode: 200,
      body: wireSession,
    }).as("appleCallback");

    cy.visit("/sign-in?next=/me", {
      onBeforeLoad(win) {
        installStubs(win, {
          authorization: { id_token: "stub-apple-id-token", code: "stub-apple-code" },
          user: { name: { firstName: "Ada", lastName: "Lovelace" } },
        });
      },
    });

    cy.wait("@refresh");

    cy.get('[data-testid="apple-signin-button"]').should("be.visible").and("not.be.disabled");
    cy.get('[data-testid="apple-signin-button"]').click();

    cy.wait("@appleCallback").its("request.body").should("deep.equal", {
      idToken: "stub-apple-id-token",
      code: "stub-apple-code",
      name: "Ada Lovelace",
    });

    cy.location("pathname").should("eq", "/me");
    cy.get('[data-testid="me-display-name"]').should("have.text", "Ada Lovelace");
    cy.get('[data-testid="me-email"]').should("have.text", "ada@example.com");
  });

  it("Apple-relay-email account signs in without the FE choking on the email shape", () => {
    cy.intercept("POST", "/api/auth/apple/callback", {
      statusCode: 200,
      body: wireRelaySession,
    }).as("appleCallbackRelay");

    cy.visit("/sign-in?next=/me", {
      onBeforeLoad(win) {
        installStubs(win, {
          authorization: { id_token: "stub-apple-id-token", code: "stub-apple-code" },
          // No `user` block on relay — Apple omits it after first sign-in.
        });
      },
    });

    cy.wait("@refresh");

    cy.get('[data-testid="apple-signin-button"]').should("be.visible").and("not.be.disabled").click();

    cy.wait("@appleCallbackRelay").its("request.body").should("deep.equal", {
      idToken: "stub-apple-id-token",
      code: "stub-apple-code",
    });

    cy.location("pathname").should("eq", "/me");
    cy.get('[data-testid="me-email"]').should(
      "have.text",
      "abc123xyz@privaterelay.appleid.com",
    );
  });

  it("linkRequired response shows the generic cross-provider error", () => {
    cy.intercept("POST", "/api/auth/apple/callback", {
      statusCode: 200,
      body: { linkRequired: true, linkProvider: "google", linkToken: "link-jwt" },
    }).as("appleCallbackLink");

    cy.visit("/sign-in", {
      onBeforeLoad(win) {
        installStubs(win, {
          authorization: { id_token: "stub-apple-id-token", code: "stub-apple-code" },
        });
      },
    });

    cy.wait("@refresh");
    cy.get('[data-testid="apple-signin-button"]').should("be.visible").and("not.be.disabled").click();
    cy.wait("@appleCallbackLink");

    cy.location("pathname").should("eq", "/sign-in");
    cy.get('[data-testid="sign-in-error"]').should(
      "contain.text",
      "registered with another provider",
    );
  });
});
