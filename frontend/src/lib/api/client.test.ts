import { describe, expect, it, vi } from "vitest";

import { apiServerRoot, createApiClient } from "./client";
import { ApiError } from "./errors";

function json(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

function envelope(code: string, message: string, details: unknown = null) {
  return { error: { code, message, details, request_id: "req-123" } };
}

function setup(respond: (request: Request) => Response | Promise<Response>) {
  const fetchMock = vi.fn(async (request: Request) => respond(request));
  const getAccessToken = vi.fn<() => string | null>(() => "token-1");
  const api = createApiClient({
    baseUrl: "http://api.test/",
    getAccessToken,
    fetch: fetchMock,
  });
  return { api, fetchMock, getAccessToken };
}

async function rejection(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError);
    return error as ApiError;
  }
  throw new Error("expected the call to reject");
}

describe("apiServerRoot", () => {
  it.each([
    ["http://localhost:8000/api", "http://localhost:8000"],
    ["http://localhost:8000/api/", "http://localhost:8000"],
    ["https://host.test/backend/api", "https://host.test/backend"],
    ["http://localhost:8000", "http://localhost:8000"],
  ])("%s -> %s", (input, expected) => {
    expect(apiServerRoot(input)).toBe(expected);
  });
});

describe("createApiClient", () => {
  it("calls the health endpoint on the server root and returns typed data", async () => {
    const { api, fetchMock } = setup(() => json({ status: "ok" }));

    const { data } = await api.GET("/healthz");

    expect(data?.status).toBe("ok");
    const request = fetchMock.mock.calls[0]![0];
    expect(request.method).toBe("GET");
    expect(request.url).toBe("http://api.test/healthz");
  });

  it("sends the JSON body and no Authorization header when there is no token", async () => {
    const { api, fetchMock, getAccessToken } = setup(() =>
      json({ access: "a", refresh: "r" }),
    );
    getAccessToken.mockReturnValue(null);

    const { data } = await api.POST("/api/v1/auth/login/", {
      body: { email: "user@example.com", password: "YOUR_PASSWORD" },
    });

    expect(data?.access).toBe("a");
    const request = fetchMock.mock.calls[0]![0];
    expect(request.url).toBe("http://api.test/api/v1/auth/login/");
    expect(request.headers.get("Authorization")).toBeNull();
    expect(await request.json()).toEqual({
      email: "user@example.com",
      password: "YOUR_PASSWORD",
    });
  });

  it("injects the bearer token from the getter on every request", async () => {
    const { api, fetchMock, getAccessToken } = setup(() =>
      json({
        id: "1",
        email: "user@example.com",
        name: "User",
        is_staff: false,
        last_login: null,
        created_at: "2026-01-01T00:00:00Z",
      }),
    );

    await api.GET("/api/v1/auth/me/");
    getAccessToken.mockReturnValue("token-2");
    await api.GET("/api/v1/auth/me/");

    const sent = fetchMock.mock.calls.map(([request]) =>
      request.headers.get("Authorization"),
    );
    expect(sent).toEqual(["Bearer token-1", "Bearer token-2"]);
  });

  it("supports an async token getter", async () => {
    const fetchMock = vi.fn<(request: Request) => Promise<Response>>(async () =>
      json({ status: "ok" }),
    );
    const api = createApiClient({
      baseUrl: "http://api.test",
      getAccessToken: async () => "async-token",
      fetch: fetchMock,
    });

    await api.GET("/healthz");

    expect(fetchMock.mock.calls[0]![0].headers.get("Authorization")).toBe(
      "Bearer async-token",
    );
  });

  it("normalises a validation envelope into ApiError", async () => {
    const { api } = setup(() =>
      json(
        envelope("validation_error", "Request validation failed.", {
          email: ["Required."],
        }),
        {
          status: 400,
          headers: {
            "Content-Type": "application/json",
            "X-Request-ID": "header-id",
          },
        },
      ),
    );

    const error = await rejection(
      api.POST("/api/v1/auth/login/", { body: { email: "", password: "" } }),
    );

    expect(error).toMatchObject({
      name: "ApiError",
      status: 400,
      code: "validation_error",
      message: "Request validation failed.",
      requestId: "req-123", // the envelope wins over the header
    });
    expect(error.fieldErrors).toEqual({ email: ["Required."] });
    expect(error.retryAfter).toBeNull();
  });

  it("exposes retry_after for throttled responses", async () => {
    const { api } = setup(() =>
      json(
        envelope("throttled", "Request was throttled.", { retry_after: 42 }),
        { status: 429 },
      ),
    );

    const error = await rejection(api.GET("/api/v1/auth/me/"));

    expect(error.code).toBe("throttled");
    expect(error.retryAfter).toBe(42);
  });

  it("reports 401 as unauthorized", async () => {
    const { api } = setup(() =>
      json(
        envelope(
          "not_authenticated",
          "Authentication credentials were not provided.",
        ),
        {
          status: 401,
        },
      ),
    );

    const error = await rejection(api.GET("/api/v1/auth/me/"));

    expect(error.isUnauthorized).toBe(true);
    expect(error.code).toBe("not_authenticated");
  });

  it("falls back to http_error for a body that is not the envelope", async () => {
    const { api } = setup(
      () =>
        new Response("<html>Bad gateway</html>", {
          status: 502,
          statusText: "Bad Gateway",
        }),
    );

    const error = await rejection(api.GET("/healthz"));

    expect(error).toMatchObject({
      status: 502,
      code: "http_error",
      message: "HTTP 502 Bad Gateway",
    });
  });

  it("keeps the body of non-envelope errors such as a 503 from /readyz", async () => {
    const body = {
      status: "unavailable",
      checks: { database: { status: "fail" } },
    };
    const { api } = setup(() => json(body, { status: 503 }));

    const error = await rejection(api.GET("/readyz"));

    expect(error.status).toBe(503);
    expect(error.code).toBe("http_error");
    expect(error.body).toEqual(body);
  });

  it("turns a failed fetch into a network_error", async () => {
    const cause = new TypeError("Failed to fetch");
    const { api } = setup(() => {
      throw cause;
    });

    const error = await rejection(api.GET("/healthz"));

    expect(error).toMatchObject({ status: 0, code: "network_error" });
    expect(error.cause).toBe(cause);
  });

  it("lets aborts through untouched", async () => {
    const abort = new DOMException("Aborted", "AbortError");
    const { api } = setup(() => {
      throw abort;
    });

    await expect(api.GET("/healthz")).rejects.toBe(abort);
  });

  it("uses the global fetch resolved at call time by default", async () => {
    const globalFetch = vi.fn(async () => json({ status: "ok" }));
    vi.stubGlobal("fetch", globalFetch);
    try {
      const api = createApiClient({ baseUrl: "http://api.test" });
      await api.GET("/healthz");
      expect(globalFetch).toHaveBeenCalledOnce();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
