/**
 * Slice-1 sign-in smoke test.
 *
 * Stubs the Google Identity Services SDK by injecting
 * `window.__threadloopGoogleIdStub__` before the page loads, so the page
 * never reaches the real `accounts.google.com/gsi/client`. The stub captures
 * the credential callback registered by the page; we trigger it from the
 * test, the page exchanges the (fake) credential against an intercepted
 * `POST /api/auth/google/callback`, lands on `/me`, and we assert the user
 * is shown.
 */

import type { GoogleCredentialResponse, GoogleIdApi } from "../../src/auth/google";

// Wire is camelCase per ADR 0009 — keys here mirror what the backend
// actually serializes (no FE adapter to translate snake_case anymore).
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
  accessToken: "stub-access-jwt",
  expiresAt: "2030-01-01T00:00:00Z",
  user: wireUser,
};

describe("ThreadLoop sign-in (slice 1: Google)", () => {
  beforeEach(() => {
    cy.intercept("POST", "/api/auth/refresh", { statusCode: 401, body: { code: "no", message: "no" } }).as(
      "refresh",
    );
    cy.intercept("POST", "/api/auth/google/callback", {
      statusCode: 200,
      body: wireSession,
    }).as("googleCallback");
    cy.intercept("GET", "/api/health", {
      statusCode: 200,
      body: { status: "ok", version: "0.1.0", db: "ok", redis: "ok", meili: "ok" },
    });
  });

  it("Google flow → lands signed in on /me", () => {
    cy.visit("/sign-in?next=/me", {
      onBeforeLoad(win) {
        let captured: ((resp: GoogleCredentialResponse) => void) | null = null;
        const stub: GoogleIdApi = {
          initialize: (config) => {
            captured = config.callback;
          },
          renderButton: (parent) => {
            const btn = win.document.createElement("button");
            btn.textContent = "Sign in with Google (stub)";
            btn.type = "button";
            btn.setAttribute("data-cy", "google-signin-stub-button");
            btn.addEventListener("click", () => {
              if (captured) captured({ credential: "stub-google-id-token" });
            });
            parent.appendChild(btn);
          },
          prompt: () => {},
          cancel: () => {},
          disableAutoSelect: () => {},
        };
        (win as Window).__threadloopGoogleIdStub__ = stub;
      },
    });

    cy.wait("@refresh");

    cy.get('[data-testid="google-button-container"]').should("exist");
    cy.get('[data-cy="google-signin-stub-button"]').should("be.visible").click();

    cy.wait("@googleCallback").its("request.body").should("deep.equal", {
      idToken: "stub-google-id-token",
    });

    cy.location("pathname").should("eq", "/me");
    cy.get('[data-testid="me-display-name"]').should("have.text", "Ada Lovelace");
    cy.get('[data-testid="me-email"]').should("have.text", "ada@example.com");
    cy.get('[data-testid="app-header-display-name"]').should("have.text", "Ada Lovelace");
  });

  it("linkRequired response shows a generic error instead of redirecting", () => {
    cy.intercept("POST", "/api/auth/google/callback", {
      statusCode: 200,
      body: { linkRequired: true, linkProvider: "apple", linkToken: "link-jwt" },
    }).as("googleCallbackLink");

    cy.visit("/sign-in", {
      onBeforeLoad(win) {
        let captured: ((resp: GoogleCredentialResponse) => void) | null = null;
        const stub: GoogleIdApi = {
          initialize: (config) => {
            captured = config.callback;
          },
          renderButton: (parent) => {
            const btn = win.document.createElement("button");
            btn.type = "button";
            btn.textContent = "Sign in with Google (stub)";
            btn.setAttribute("data-cy", "google-signin-stub-button");
            btn.addEventListener("click", () => {
              if (captured) captured({ credential: "stub-google-id-token" });
            });
            parent.appendChild(btn);
          },
          prompt: () => {},
          cancel: () => {},
          disableAutoSelect: () => {},
        };
        (win as Window).__threadloopGoogleIdStub__ = stub;
      },
    });

    cy.wait("@refresh");
    cy.get('[data-cy="google-signin-stub-button"]').should("be.visible").click();
    cy.wait("@googleCallbackLink");

    cy.location("pathname").should("eq", "/sign-in");
    cy.get('[data-testid="sign-in-error"]').should(
      "contain.text",
      "registered with another provider",
    );
  });
});

