import { afterEach, describe, expect, it, vi } from "vitest";

async function loadConfig() {
  vi.resetModules();
  return (await import("./config")).config;
}

describe("config", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("throws a helpful error when NEXT_PUBLIC_API_BASE_URL is missing", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "");
    await expect(loadConfig()).rejects.toThrow(/NEXT_PUBLIC_API_BASE_URL/);
  });

  it("returns the API base URL", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://localhost:8000/api");
    const config = await loadConfig();
    expect(config.apiBaseUrl).toBe("http://localhost:8000/api");
  });

  it("strips trailing slashes", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://localhost:8000/api//");
    const config = await loadConfig();
    expect(config.apiBaseUrl).toBe("http://localhost:8000/api");
  });
});
