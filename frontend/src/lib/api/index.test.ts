import { afterEach, describe, expect, it, vi } from "vitest";

async function load() {
  vi.resetModules();
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://localhost:8000/api");
  return import("./index");
}

describe("getApiClient", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("builds URLs from the configured base URL and reuses one client", async () => {
    const fetchMock = vi.fn(async () => Response.json({ status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);
    const { getApiClient } = await load();

    await getApiClient().GET("/healthz");

    const request = (fetchMock.mock.calls[0] as unknown as [Request])[0];
    expect(request.url).toBe("http://localhost:8000/healthz");
    expect(getApiClient()).toBe(getApiClient());
  });

  it("sends the token from the pluggable getter, and none once it is removed", async () => {
    const fetchMock = vi.fn(async () => Response.json({ status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);
    const { getApiClient, setAccessTokenGetter } = await load();

    setAccessTokenGetter(() => "live-token");
    await getApiClient().GET("/healthz");
    setAccessTokenGetter(undefined);
    await getApiClient().GET("/healthz");

    const auth = fetchMock.mock.calls.map((call) =>
      (call as unknown as [Request])[0].headers.get("Authorization"),
    );
    expect(auth).toEqual(["Bearer live-token", null]);
  });
});
