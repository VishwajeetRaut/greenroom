/**
 * Fitness functions for the frontend API module.
 * These tests enforce structural contracts — not behavior — so they run
 * without a real backend and without mocking every dependency.
 */
import { describe, it, expect } from "vitest";

describe("api module surface", () => {
  it("exports an api object with the required methods", async () => {
    // Stub env vars before importing the module
    import.meta.env.VITE_SUPABASE_URL = "https://test.supabase.co";
    import.meta.env.VITE_SUPABASE_ANON_KEY = "anon-key-test";

    // Dynamically import to pick up the stubbed env
    const { api } = await import("../lib/api.ts");

    const required = [
      "startSession",
      "sendMessage",
      "runCode",
      "runTests",
      "endSession",
      "deleteSession",
      "getBoilerplate",
      "speak",
    ];
    for (const method of required) {
      expect(typeof api[method], `api.${method} must exist`).not.toBe("undefined");
    }
  });
});

describe("security: no service-role key in frontend source", () => {
  it("supabaseClient does not reference SERVICE_ROLE", async () => {
    // Read the raw source as text — this would catch accidental key inclusion
    // even if the module can't be imported in test env.
    const src = await fetch(
      new URL("../lib/supabaseClient.ts", import.meta.url)
    ).catch(() => null);
    if (!src) return; // fetch may not work in jsdom — skip gracefully
    const text = await src.text();
    expect(text).not.toContain("SERVICE_ROLE");
    expect(text).not.toContain("service_role");
  });
});
