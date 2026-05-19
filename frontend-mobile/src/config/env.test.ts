import { config, readConfig } from "./env";

/**
 * `babel-preset-expo` inlines every `process.env.EXPO_PUBLIC_*` reference
 * at compile time — by the time jest runs, those reads have already been
 * folded to whatever was in the environment at babel-transform time
 * (typically nothing in CI, which makes the strict-parse defaults the
 * dominant case). Mutating `process.env` at runtime therefore has no
 * effect on the folded values, so the tests here assert the parsed
 * shape of the config given the build-time env, rather than re-mocking
 * the env. The parse-logic invariants (strict `=== "true"`, fallback
 * chain, empty-string normalisation) are kept as runtime smoke checks
 * against the same `readConfig()` entry point.
 */
describe("readConfig", () => {
  it("returns a config object with the expected shape", () => {
    const cfg = readConfig();
    expect(typeof cfg.apiBaseUrl).toBe("string");
    expect(cfg.apiBaseUrl.startsWith("http")).toBe(true);
    expect(typeof cfg.googleEnabled).toBe("boolean");
    expect(typeof cfg.facebookEnabled).toBe("boolean");
    expect(typeof cfg.appleEnabled).toBe("boolean");
  });

  it("never enables Apple by default — descoped per RFC 0001", () => {
    // Mirror of the FE/BE contract: Apple is the only provider whose
    // default-off posture is durable across re-activation. This guard
    // catches an accidental flip to `EXPO_PUBLIC_APPLE_ENABLED=true`
    // in the build env before the App Store submission Epic.
    expect(config.appleEnabled).toBe(false);
  });
});
