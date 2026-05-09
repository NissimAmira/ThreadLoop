import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { loadFacebookIdentity, type FacebookSdkApi } from "./facebook";

function makeStub(): FacebookSdkApi {
  return {
    init: () => {},
    login: (cb) =>
      cb({
        status: "connected",
        authResponse: { accessToken: "EAA-stub", userID: "1" },
      }),
  };
}

describe("loadFacebookIdentity", () => {
  beforeEach(() => {
    delete window.__threadloopFacebookIdStub__;
    delete window.FB;
  });

  afterEach(() => {
    delete window.__threadloopFacebookIdStub__;
    delete window.FB;
  });

  it("returns the test stub when one is installed", async () => {
    const stub = makeStub();
    window.__threadloopFacebookIdStub__ = stub;
    await expect(loadFacebookIdentity()).resolves.toBe(stub);
  });

  it("returns the SDK shape from window.FB when present", async () => {
    const sdk = makeStub();
    window.FB = sdk;
    await expect(loadFacebookIdentity()).resolves.toBe(sdk);
  });
});
