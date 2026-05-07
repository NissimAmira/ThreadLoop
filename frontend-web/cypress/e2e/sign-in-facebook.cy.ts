/**
 * Slice-3 Facebook sign-in smoke test (#39).
 *
 * Stubs the Facebook Login SDK by injecting
 * `window.__threadloopFacebookIdStub__` before the page loads, so the page
 * never reaches the real `connect.facebook.net/...`. The stub captures the
 * config from `init()` and returns a fake `FacebookLoginResponse` from
 * `login()`. We click the FE-rendered Facebook button, the page exchanges
 * the (fake) access token against an intercepted
 * `POST /api/auth/facebook/callback`, lands on `/me`, and we assert the
 * user is shown.
 *
 * Mirrors `sign-in.cy.ts` (Google) and `sign-in-apple.cy.ts` (Apple) — the
 * three providers' flows live in the same page; we keep the spec separate
 * so each provider's smoke can run / fail independently.
 *
 * Note: this stack runs with `VITE_FACEBOOK_ENABLED` defaulting to `false`
 * in the dev `.env.example`. Cypress' default config inherits from the
 * Vite dev server, which means the flag is `false` by default at runtime.
 * The smoke pre-installs the stub *and* sets `VITE_FACEBOOK_ENABLED=true`
 * via `cy.intercept` is not how Vite envs work — instead, we rely on
 * the project owner running `npx cypress run` against a dev stack that
 * has `VITE_FACEBOOK_ENABLED=true` in `frontend-web/.env`. When invoked
 * from CI without that flag set, the test will skip (the FB button isn't
 * in the tree). The same pattern applies to slice-2 Apple.
 */

import type {
  FacebookLoginResponse,
  FacebookSdkApi,
} from "../../src/auth/facebook";
import type { GoogleIdApi } from "../../src/auth/google";

// Wire is camelCase per ADR 0009 — keys here mirror what the backend
// actually serializes (no FE adapter to translate snake_case anymore).
const wireUser = {
  id: "00000000-0000-0000-0000-000000000003",
  provider: "facebook",
  email: "ada@example.com",
  emailVerified: false,
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

// Email-permission-decline branch: user declined the `email` scope, so
// the BE persists `email=null` and falls back to display_name. The FE
// completes sign-in without erroring.
const wireNoEmailSession = {
  ...wireSession,
  user: {
    ...wireUser,
    email: null,
  },
};

function installStubs(
  win: Cypress.AUTWindow,
  fbResponse: FacebookLoginResponse,
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

  const facebookStub: FacebookSdkApi = {
    init: () => {},
    login: (cb) => cb(fbResponse),
  };
  (win as Window).__threadloopFacebookIdStub__ = facebookStub;
}

describe("ThreadLoop sign-in (slice 3: Facebook)", () => {
  beforeEach(() => {
    cy.intercept("POST", "/api/auth/refresh", {
      statusCode: 401,
      body: { code: "no", message: "no" },
    }).as("refresh");
    cy.intercept("GET", "/api/health", {
      statusCode: 200,
      body: {
        status: "ok",
        version: "0.1.0",
        db: "ok",
        redis: "ok",
        meili: "ok",
      },
    });
  });

  it("Facebook flow → lands signed in on /me", () => {
    cy.intercept("POST", "/api/auth/facebook/callback", {
      statusCode: 200,
      body: wireSession,
    }).as("facebookCallback");

    cy.visit("/sign-in?next=/me", {
      onBeforeLoad(win) {
        installStubs(win, {
          status: "connected",
          authResponse: {
            accessToken: "stub-fb-user-token",
            userID: "12345",
          },
        });
      },
    });

    cy.wait("@refresh");

    cy.get('[data-testid="facebook-signin-button"]')
      .should("be.visible")
      .and("not.be.disabled");
    cy.get('[data-testid="facebook-signin-button"]').click();

    cy.wait("@facebookCallback")
      .its("request.body")
      .should("deep.equal", { accessToken: "stub-fb-user-token" });

    cy.location("pathname").should("eq", "/me");
    cy.get('[data-testid="me-display-name"]').should(
      "have.text",
      "Ada Lovelace",
    );
    cy.get('[data-testid="me-email"]').should("have.text", "ada@example.com");
  });

  it("Email-permission-decline (BE returns email=null) signs in cleanly", () => {
    cy.intercept("POST", "/api/auth/facebook/callback", {
      statusCode: 200,
      body: wireNoEmailSession,
    }).as("facebookCallbackNoEmail");

    cy.visit("/sign-in?next=/me", {
      onBeforeLoad(win) {
        installStubs(win, {
          status: "connected",
          authResponse: {
            accessToken: "stub-fb-user-token",
            userID: "12345",
          },
        });
      },
    });

    cy.wait("@refresh");
    cy.get('[data-testid="facebook-signin-button"]')
      .should("be.visible")
      .and("not.be.disabled")
      .click();
    cy.wait("@facebookCallbackNoEmail");

    // Sign-in completes: we land on /me even though the BE returned email=null.
    cy.location("pathname").should("eq", "/me");
    cy.get('[data-testid="me-display-name"]').should(
      "have.text",
      "Ada Lovelace",
    );
  });

  it("User-cancel (status != connected) does not surface a scary error", () => {
    cy.visit("/sign-in", {
      onBeforeLoad(win) {
        installStubs(win, {
          status: "unknown",
          authResponse: null,
        });
      },
    });

    cy.wait("@refresh");
    cy.get('[data-testid="facebook-signin-button"]')
      .should("be.visible")
      .and("not.be.disabled")
      .click();

    // Stays on /sign-in; error region empty.
    cy.location("pathname").should("eq", "/sign-in");
    cy.get('[data-testid="sign-in-error"]').should("have.text", "");
    cy.get('[data-testid="facebook-signin-button"]').should("not.be.disabled");
  });
});
