import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  composeAppleDisplayName,
  loadAppleIdentity,
  type AppleIdAuthApi,
  type AppleSignInResponse,
} from "./apple";

function makeStub(): AppleIdAuthApi {
  return {
    init: () => {},
    signIn: () =>
      Promise.resolve<AppleSignInResponse>({
        authorization: { id_token: "id-token", code: "code" },
      }),
  };
}

describe("loadAppleIdentity", () => {
  beforeEach(() => {
    delete window.__threadloopAppleIdStub__;
    delete window.AppleID;
  });

  afterEach(() => {
    delete window.__threadloopAppleIdStub__;
    delete window.AppleID;
  });

  it("returns the test stub when one is installed", async () => {
    const stub = makeStub();
    window.__threadloopAppleIdStub__ = stub;
    await expect(loadAppleIdentity()).resolves.toBe(stub);
  });

  it("returns the SDK shape from window.AppleID.auth when present", async () => {
    const sdk = makeStub();
    window.AppleID = { auth: sdk };
    await expect(loadAppleIdentity()).resolves.toBe(sdk);
  });
});

describe("composeAppleDisplayName", () => {
  it("returns first + last when both are present", () => {
    expect(
      composeAppleDisplayName({ name: { firstName: "Ada", lastName: "Lovelace" } }),
    ).toBe("Ada Lovelace");
  });

  it("returns only the present half if one is missing", () => {
    expect(composeAppleDisplayName({ name: { firstName: "Ada" } })).toBe("Ada");
    expect(composeAppleDisplayName({ name: { lastName: "Lovelace" } })).toBe(
      "Lovelace",
    );
  });

  it("trims whitespace and collapses to undefined when nothing useful remains", () => {
    expect(
      composeAppleDisplayName({ name: { firstName: "  ", lastName: "" } }),
    ).toBeUndefined();
  });

  it("returns undefined when user is missing entirely", () => {
    expect(composeAppleDisplayName(undefined)).toBeUndefined();
  });
});
