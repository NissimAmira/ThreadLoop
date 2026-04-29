import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StatusBar } from "./StatusBar";

describe("StatusBar", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("renders green pill when API reports ok", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ status: "ok", version: "0.1.0", db: "ok", redis: "ok", meili: "ok" }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );
    render(<StatusBar />);
    await waitFor(() => {
      expect(screen.getByTestId("status-bar").dataset.status).toBe("ok");
    });
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/health"), expect.any(Object));
  });

  it("renders red pill when API is unreachable", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network"));
    render(<StatusBar />);
    await waitFor(() => {
      expect(screen.getByTestId("status-bar").dataset.status).toBe("down");
    });
  });
});
