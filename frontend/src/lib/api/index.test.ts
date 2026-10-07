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

  it("retries once with the token from the 401 handler", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json(
          { error: { code: "authentication_failed", message: "x" } },
          { status: 401 },
        ),
      )
      .mockResolvedValueOnce(Response.json({ status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);
    const { getApiClient, setAccessTokenGetter, setUnauthorizedHandler } =
      await load();
    const handler = vi.fn(async () => "fresh-token");
    setAccessTokenGetter(() => "stale-token");
    setUnauthorizedHandler(handler);

    const { data } = await getApiClient().GET("/healthz");

    expect(data).toEqual({ status: "ok" });
    expect(handler).toHaveBeenCalledTimes(1);
    const sent = fetchMock.mock.calls.map((call) =>
      (call as unknown as [Request])[0].headers.get("Authorization"),
    );
    expect(sent).toEqual(["Bearer stale-token", "Bearer fresh-token"]);
  });

  it("surfaces the 401 when the handler has no new token", async () => {
    const fetchMock = vi.fn(async () =>
      Response.json(
        { error: { code: "authentication_failed", message: "x" } },
        { status: 401 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { ApiError, getApiClient, setUnauthorizedHandler } = await load();
    setUnauthorizedHandler(async () => null);

    await expect(getApiClient().GET("/healthz")).rejects.toBeInstanceOf(
      ApiError,
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not retry a second 401 forever", async () => {
    const fetchMock = vi.fn(async () =>
      Response.json(
        { error: { code: "authentication_failed", message: "x" } },
        { status: 401 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { ApiError, getApiClient, setUnauthorizedHandler } = await load();
    setUnauthorizedHandler(async () => "t");

    await expect(getApiClient().GET("/healthz")).rejects.toBeInstanceOf(
      ApiError,
    );
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
