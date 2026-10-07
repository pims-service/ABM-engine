// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { REFRESH_COOKIE_NAME } from "@/lib/auth/constants";

import { POST as loginPOST } from "./login/route";
import { POST as logoutPOST } from "./logout/route";
import { GET as meGET } from "./me/route";
import { POST as refreshPOST } from "./refresh/route";

// Placeholder only; never a real credential. pragma: allowlist secret
const PASSWORD = "placeholder-password"; // pragma: allowlist secret

function jwt(exp: number): string {
  const encode = (value: unknown) =>
    Buffer.from(JSON.stringify(value)).toString("base64url");
  return `${encode({ alg: "none" })}.${encode({ exp })}.sig`;
}

const NOW_S = Math.floor(Date.now() / 1000);
const ACCESS = jwt(NOW_S + 900);
const REFRESH = jwt(NOW_S + 7 * 86400);
const NEXT_REFRESH = jwt(NOW_S + 7 * 86400 + 5);

const fetchMock = vi.fn();

function post(path: string, body?: unknown, cookie?: string) {
  return new NextRequest(`http://localhost:3000${path}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(cookie ? { cookie: `${REFRESH_COOKIE_NAME}=${cookie}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

function envelope(status: number, code: string, details: unknown = null) {
  return Response.json(
    { error: { code, message: "upstream text", details, request_id: "r1" } },
    { status },
  );
}

function lastUpstream() {
  const [url, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit];
  return {
    url,
    init,
    body: init.body ? JSON.parse(init.body as string) : null,
  };
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  vi.stubEnv("API_INTERNAL_BASE_URL", "http://api.internal:8000/api");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("POST /api/auth/login", () => {
  it("returns only the access token and sets the refresh token as an httpOnly cookie", async () => {
    fetchMock.mockResolvedValue(
      Response.json({ access: ACCESS, refresh: REFRESH }),
    );

    const response = await loginPOST(
      post("/api/auth/login", { email: "ada@example.com", password: PASSWORD }),
    );

    expect(lastUpstream().url).toBe(
      "http://api.internal:8000/api/v1/auth/login/",
    );
    expect(lastUpstream().body).toEqual({
      email: "ada@example.com",
      password: PASSWORD,
    });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      access: ACCESS,
      expires_at: NOW_S + 900,
    });
    expect(response.headers.get("cache-control")).toBe("no-store");

    const cookie = response.cookies.get(REFRESH_COOKIE_NAME);
    expect(cookie).toMatchObject({
      value: REFRESH,
      httpOnly: true,
      sameSite: "lax",
      path: "/",
    });
    expect(cookie?.maxAge).toBeGreaterThan(6 * 86400);
  });

  it("marks the cookie Secure in production only", async () => {
    fetchMock.mockResolvedValue(
      Response.json({ access: ACCESS, refresh: REFRESH }),
    );
    vi.stubEnv("NODE_ENV", "production");
    const response = await loginPOST(
      post("/api/auth/login", { email: "a@b.co", password: PASSWORD }),
    );
    expect(response.cookies.get(REFRESH_COOKIE_NAME)?.secure).toBe(true);

    vi.stubEnv("NODE_ENV", "development");
    const dev = await loginPOST(
      post("/api/auth/login", { email: "a@b.co", password: PASSWORD }),
    );
    expect(dev.cookies.get(REFRESH_COOKIE_NAME)?.secure).toBeFalsy();
  });

  it("forwards the API's error envelope and sets no cookie", async () => {
    fetchMock.mockResolvedValue(envelope(401, "authentication_failed"));
    const response = await loginPOST(
      post("/api/auth/login", { email: "a@b.co", password: PASSWORD }),
    );
    expect(response.status).toBe(401);
    expect((await response.json()).error.code).toBe("authentication_failed");
    expect(response.cookies.get(REFRESH_COOKIE_NAME)).toBeUndefined();
  });

  it("rejects bad input without calling the API", async () => {
    const response = await loginPOST(
      post("/api/auth/login", { email: "a@b.co" }),
    );
    expect(response.status).toBe(400);
    expect((await response.json()).error.details).toEqual({
      password: ["This field is required."],
    });
    const notJson = await loginPOST(
      new NextRequest("http://localhost:3000/api/auth/login", {
        method: "POST",
        body: "nope",
      }),
    );
    expect(notJson.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reports an unreachable API as 502", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));
    const response = await loginPOST(
      post("/api/auth/login", { email: "a@b.co", password: PASSWORD }),
    );
    expect(response.status).toBe(502);
    expect((await response.json()).error.code).toBe("upstream_unavailable");
  });

  it("explains a backend running in cookie mode (no refresh token in the body)", async () => {
    fetchMock.mockResolvedValue(Response.json({ access: ACCESS }));
    const response = await loginPOST(
      post("/api/auth/login", { email: "a@b.co", password: PASSWORD }),
    );
    expect(response.status).toBe(502);
    const { error } = await response.json();
    expect(error.code).toBe("bff_misconfigured");
    expect(error.message).toContain("AUTH_REFRESH_COOKIE_ENABLED=false");
  });
});

describe("POST /api/auth/refresh", () => {
  it("sends the cookie's token to the API and rotates the cookie", async () => {
    fetchMock.mockResolvedValue(
      Response.json({ access: ACCESS, refresh: NEXT_REFRESH }),
    );
    const response = await refreshPOST(
      post("/api/auth/refresh", undefined, REFRESH),
    );

    expect(lastUpstream().url).toBe(
      "http://api.internal:8000/api/v1/auth/refresh/",
    );
    expect(lastUpstream().body).toEqual({ refresh: REFRESH });
    expect(response.cookies.get(REFRESH_COOKIE_NAME)?.value).toBe(NEXT_REFRESH);
    expect((await response.json()).access).toBe(ACCESS);
  });

  it("keeps the current cookie value when the API does not rotate", async () => {
    fetchMock.mockResolvedValue(Response.json({ access: ACCESS }));
    const response = await refreshPOST(
      post("/api/auth/refresh", undefined, REFRESH),
    );
    expect(response.cookies.get(REFRESH_COOKIE_NAME)?.value).toBe(REFRESH);
  });

  it("answers 401 without calling the API when there is no cookie", async () => {
    const response = await refreshPOST(post("/api/auth/refresh"));
    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("clears a cookie the API rejects", async () => {
    fetchMock.mockResolvedValue(envelope(401, "authentication_failed"));
    const response = await refreshPOST(
      post("/api/auth/refresh", undefined, REFRESH),
    );
    expect(response.status).toBe(401);
    expect(response.cookies.get(REFRESH_COOKIE_NAME)).toMatchObject({
      value: "",
      maxAge: 0,
    });
  });

  it("keeps the cookie when the API is down", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));
    const response = await refreshPOST(
      post("/api/auth/refresh", undefined, REFRESH),
    );
    expect(response.status).toBe(502);
    expect(response.cookies.get(REFRESH_COOKIE_NAME)).toBeUndefined();
  });

  it("wraps a non-envelope upstream failure", async () => {
    fetchMock.mockResolvedValue(
      new Response("<html>bad gateway</html>", { status: 503 }),
    );
    const response = await refreshPOST(
      post("/api/auth/refresh", undefined, REFRESH),
    );
    expect(response.status).toBe(502);
    expect((await response.json()).error.code).toBe("upstream_error");
    expect(response.cookies.get(REFRESH_COOKIE_NAME)).toBeUndefined();
  });
});

describe("POST /api/auth/logout", () => {
  it("blacklists the refresh token at the API and clears the cookie", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    const response = await logoutPOST(
      post("/api/auth/logout", undefined, REFRESH),
    );

    expect(lastUpstream().url).toBe(
      "http://api.internal:8000/api/v1/auth/logout/",
    );
    expect(lastUpstream().body).toEqual({ refresh: REFRESH });
    expect(response.status).toBe(204);
    expect(response.cookies.get(REFRESH_COOKIE_NAME)).toMatchObject({
      value: "",
      maxAge: 0,
    });
  });

  it("clears the cookie even when the API is unreachable or there is no cookie", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));
    const down = await logoutPOST(post("/api/auth/logout", undefined, REFRESH));
    expect(down.status).toBe(204);
    expect(down.cookies.get(REFRESH_COOKIE_NAME)?.maxAge).toBe(0);

    fetchMock.mockReset();
    const none = await logoutPOST(post("/api/auth/logout"));
    expect(none.status).toBe(204);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("GET /api/auth/me", () => {
  it("proxies the current user using the caller's access token", async () => {
    const user = { id: "u1", email: "ada@example.com", name: "Ada" };
    fetchMock.mockResolvedValue(Response.json(user));
    const response = await meGET(
      new NextRequest("http://localhost:3000/api/auth/me", {
        headers: { authorization: `Bearer ${ACCESS}` },
      }),
    );
    expect(
      (lastUpstream().init.headers as Record<string, string>).Authorization,
    ).toBe(`Bearer ${ACCESS}`);
    expect(await response.json()).toEqual(user);
  });

  it("answers 401 without a token and forwards API rejections", async () => {
    const missing = await meGET(
      new NextRequest("http://localhost:3000/api/auth/me"),
    );
    expect(missing.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();

    fetchMock.mockResolvedValue(envelope(401, "authentication_failed"));
    const rejected = await meGET(
      new NextRequest("http://localhost:3000/api/auth/me", {
        headers: { authorization: "Bearer stale" },
      }),
    );
    expect(rejected.status).toBe(401);
  });
});

describe("upstream URL", () => {
  it("falls back to the public base URL", async () => {
    vi.stubEnv("API_INTERNAL_BASE_URL", "");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://localhost:8000/api");
    fetchMock.mockResolvedValue(
      Response.json({ access: ACCESS, refresh: REFRESH }),
    );
    await loginPOST(
      post("/api/auth/login", { email: "a@b.co", password: PASSWORD }),
    );
    expect(lastUpstream().url).toBe("http://localhost:8000/api/v1/auth/login/");
  });
});
