describe("ThreadLoop status bar", () => {
  it("polls /api/health and shows a status pill", () => {
    cy.intercept("GET", "/api/health", {
      statusCode: 200,
      body: { status: "ok", version: "0.1.0", db: "ok", redis: "ok", meili: "ok" },
    }).as("health");

    cy.visit("/");
    cy.wait("@health");

    cy.get('[data-testid="status-bar"]').should("have.attr", "data-status", "ok");
    cy.contains("All systems operational").should("be.visible");
  });

  it("shows degraded state when a dependency is down", () => {
    cy.intercept("GET", "/api/health", {
      statusCode: 200,
      body: { status: "down", version: "0.1.0", db: "ok", redis: "down", meili: "ok" },
    }).as("health");

    cy.visit("/");
    cy.wait("@health");

    cy.get('[data-testid="status-bar"]').should("have.attr", "data-status", "down");
  });
});
