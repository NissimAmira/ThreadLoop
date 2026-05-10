/**
 * Slice-4 link-accounts smoke (#40) — exercises the cross-provider
 * account-linking modal end-to-end at the network seam.
 *
 * Setup:
 *   - Apple is the SECOND provider. The user clicks the Apple button on
 *     `/sign-in`, the (intercepted) Apple callback returns
 *     `linkRequired: true` with `linkProvider: "google"`.
 *   - Google is the ORIGINAL provider. The modal opens with the Google
 *     button highlighted; the user clicks the GIS-rendered button inside
 *     the modal; the (intercepted) `POST /api/auth/link` returns the
 *     merged session; the page redirects to `/me`.
 *
 * Carve-out (per AC, 2026-05-10): Apple is descoped behind
 * `VITE_APPLE_ENABLED=false` in default builds, and Facebook never produces
 * real cross-provider collisions because Graph `/debug_token` doesn't carry
 * `email_verified`. The Cypress fixture stubs both SDKs at the network seam
 * so this test exercises modal/handler logic without depending on either
 * provider being live. BE-side coverage of the actual Google↔Apple↔Facebook
 * matrix lives on #18 / PR #64. The Apple stub fires here despite the
 * default-disabled flag because:
 *   1. The /sign-in page only mounts the Apple button when
 *      VITE_APPLE_ENABLED=true (we set that in cypress.config.ts).
 *   2. The link flow itself doesn't gate on the flag — the modal renders
 *      whatever provider the BE returned in `linkProvider`, since the
 *      intent is to confirm an EXISTING identity not to OFFER sign-in.
 */

import type { AppleIdAuthApi, AppleSignInResponse } from "../../src/auth/apple";
import type { GoogleCredentialResponse, GoogleIdApi } from "../../src/auth/google";

const mergedUser = {
  id: "00000000-0000-0000-0000-000000000099",
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

const mergedSession = {
  linkRequired: false,
  accessToken: "merged-access-jwt",
  expiresAt: "2030-01-01T00:00:00Z",
  user: mergedUser,
};

function installStubs(
  win: Cypress.AUTWindow,
  appleResponse: AppleSignInResponse,
  googleCredential: GoogleCredentialResponse,
) {
  let captured: ((resp: GoogleCredentialResponse) => void) | null = null;
  const googleStub: GoogleIdApi = {
    initialize: (config) => {
      captured = config.callback;
    },
    renderButton: (parent) => {
      const btn = win.document.createElement("button");
      btn.type = "button";
      btn.textContent = "Sign in with Google (stub)";
      btn.setAttribute("data-cy", "google-signin-stub-button");
      btn.addEventListener("click", () => {
        if (captured) captured(googleCredential);
      });
      parent.replaceChildren(btn);
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

describe("ThreadLoop sign-in (slice 4: account linking)", () => {
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

  it("Apple link_required → modal → Google re-auth → POST /api/auth/link → merged /me", () => {
    cy.intercept("POST", "/api/auth/apple/callback", {
      statusCode: 200,
      body: {
        linkRequired: true,
        linkProvider: "google",
        linkToken: "stub-link-jwt",
      },
    }).as("appleCallback");

    cy.intercept("POST", "/api/auth/link", (req) => {
      // Assert the BE contract surface from PR #64.
      expect(req.body).to.deep.equal({
        linkToken: "stub-link-jwt",
        originalProvider: "google",
        credential: { idToken: "stub-google-id-token" },
      });
      req.reply({ statusCode: 200, body: mergedSession });
    }).as("linkCall");

    cy.intercept("GET", "/api/me", {
      statusCode: 200,
      body: mergedUser,
    }).as("me");

    cy.visit("/sign-in?next=/me", {
      onBeforeLoad(win) {
        installStubs(
          win,
          {
            authorization: {
              id_token: "stub-apple-id-token",
              code: "stub-apple-code",
            },
          },
          { credential: "stub-google-id-token" },
        );
      },
    });

    cy.wait("@refresh");

    // Click the Apple button — fires the link_required envelope.
    cy.get('[data-testid="apple-signin-button"]')
      .should("be.visible")
      .and("not.be.disabled")
      .click();
    cy.wait("@appleCallback");

    // Modal opens with the Google original-provider button highlighted.
    cy.get('[data-testid="link-accounts-dialog"]').should("be.visible");
    cy.get('[data-testid="link-accounts-dialog"]').should(
      "contain.text",
      "Google",
    );
    cy.get('[data-testid="link-accounts-original-button-wrapper"]').should(
      "be.visible",
    );

    // a11y assertions inline — the role/aria-modal/aria-labelledby/
    // aria-describedby AC bullets need a runnable check at the network
    // boundary, not just unit.
    cy.get('[data-testid="link-accounts-dialog"]')
      .should("have.attr", "role", "dialog")
      .and("have.attr", "aria-modal", "true")
      .and("have.attr", "aria-labelledby")
      .and("have.attr", "aria-describedby");

    // Click the GIS-rendered button inside the modal — Google credential
    // resolves through GIS's captured callback into POST /api/auth/link.
    cy.get('[data-cy="google-signin-stub-button"]').last().click();
    cy.wait("@linkCall");

    cy.location("pathname").should("eq", "/me");
    cy.get('[data-testid="me-display-name"]').should("have.text", "Ada Lovelace");

    // No link_token persisted anywhere — AC bullet.
    cy.window().then((win) => {
      for (let i = 0; i < win.localStorage.length; i++) {
        const k = win.localStorage.key(i)!;
        expect(win.localStorage.getItem(k)).to.not.contain("stub-link-jwt");
      }
      for (let i = 0; i < win.sessionStorage.length; i++) {
        const k = win.sessionStorage.key(i)!;
        expect(win.sessionStorage.getItem(k)).to.not.contain("stub-link-jwt");
      }
      expect(win.document.cookie).to.not.contain("stub-link-jwt");
    });
  });

  it("expired link_token (401) shows the recovery copy + Back-to-sign-in CTA", () => {
    cy.intercept("POST", "/api/auth/apple/callback", {
      statusCode: 200,
      body: {
        linkRequired: true,
        linkProvider: "google",
        linkToken: "stub-link-jwt",
      },
    }).as("appleCallback");

    cy.intercept("POST", "/api/auth/link", {
      statusCode: 401,
      body: { code: "invalid_link_token", message: "expired" },
    }).as("linkCall401");

    cy.visit("/sign-in", {
      onBeforeLoad(win) {
        installStubs(
          win,
          {
            authorization: {
              id_token: "stub-apple-id-token",
              code: "stub-apple-code",
            },
          },
          { credential: "stub-google-id-token" },
        );
      },
    });

    cy.wait("@refresh");
    cy.get('[data-testid="apple-signin-button"]')
      .should("be.visible")
      .and("not.be.disabled")
      .click();
    cy.wait("@appleCallback");

    cy.get('[data-testid="link-accounts-dialog"]').should("be.visible");
    cy.get('[data-cy="google-signin-stub-button"]').last().click();
    cy.wait("@linkCall401");

    cy.get('[data-testid="link-accounts-status"]').should(
      "contain.text",
      "session expired",
    );
    cy.get('[data-testid="link-accounts-recovery-cta"]').should("be.visible");
  });

  it("Esc closes the modal and restores focus to the originally-clicked second-provider button", () => {
    cy.intercept("POST", "/api/auth/apple/callback", {
      statusCode: 200,
      body: {
        linkRequired: true,
        linkProvider: "google",
        linkToken: "stub-link-jwt",
      },
    }).as("appleCallback");

    cy.visit("/sign-in", {
      onBeforeLoad(win) {
        installStubs(
          win,
          {
            authorization: {
              id_token: "stub-apple-id-token",
              code: "stub-apple-code",
            },
          },
          { credential: "stub-google-id-token" },
        );
      },
    });

    cy.wait("@refresh");
    cy.get('[data-testid="apple-signin-button"]')
      .should("be.visible")
      .and("not.be.disabled")
      .click();
    cy.wait("@appleCallback");
    cy.get('[data-testid="link-accounts-dialog"]').should("be.visible");

    cy.get("body").trigger("keydown", { key: "Escape" });
    cy.get('[data-testid="link-accounts-dialog"]').should("not.exist");
    // Apple button (the originally-clicked second-provider trigger) gets
    // focus back per the WAI-ARIA APG dialog pattern.
    cy.focused().should("have.attr", "data-testid", "apple-signin-button");
  });

  it("wrong-provider re-auth surfaces an inline message in the modal", () => {
    cy.intercept("POST", "/api/auth/apple/callback", {
      statusCode: 200,
      body: {
        linkRequired: true,
        linkProvider: "google",
        linkToken: "stub-link-jwt",
      },
    }).as("appleCallback");

    cy.visit("/sign-in", {
      onBeforeLoad(win) {
        installStubs(
          win,
          {
            authorization: {
              id_token: "stub-apple-id-token",
              code: "stub-apple-code",
            },
          },
          { credential: "stub-google-id-token" },
        );
      },
    });

    cy.wait("@refresh");
    cy.get('[data-testid="apple-signin-button"]')
      .should("be.visible")
      .and("not.be.disabled")
      .click();
    cy.wait("@appleCallback");
    cy.get('[data-testid="link-accounts-dialog"]').should("be.visible");

    // Now click the Apple button (the SECOND provider) again — wrong-provider
    // path. The modal should stay up and surface an inline message; no new
    // /api/auth/apple/callback round-trip should happen.
    cy.intercept("POST", "/api/auth/apple/callback", () => {
      throw new Error("apple callback should not fire on wrong-provider click");
    }).as("appleCallbackBlocked");
    cy.get('[data-testid="apple-signin-button"]').click({ force: true });
    cy.get('[data-testid="link-accounts-dialog"]').should("be.visible");
    cy.get('[data-testid="link-accounts-status"]').should(
      "contain.text",
      "Sign in with Google",
    );
  });
});
