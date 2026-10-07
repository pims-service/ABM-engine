// @vitest-environment node
import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { REFRESH_COOKIE_NAME } from "@/lib/auth/constants";

import { config, middleware } from "./middleware";

function request(path: string, cookie?: string) {
  return new NextRequest(`http://localhost:3000${path}`, {
    headers: cookie ? { cookie } : {},
  });
}

describe("middleware", () => {
  it("redirects a visitor without a session to login, keeping path and query", () => {
    const response = middleware(request("/campaigns?status=active"));
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe(
      "http://localhost:3000/login?next=%2Fcampaigns%3Fstatus%3Dactive",
    );
  });

  it("protects the root path", () => {
    expect(middleware(request("/")).headers.get("location")).toContain(
      "/login?next=%2F",
    );
  });

  it("lets a visitor with a session cookie through", () => {
    const response = middleware(
      request("/dashboard", `${REFRESH_COOKIE_NAME}=abc`),
    );
    expect(response.headers.get("location")).toBeNull();
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });

  it("never redirects the login page itself", () => {
    expect(
      middleware(request("/login?next=%2Fdashboard")).headers.get("location"),
    ).toBeNull();
  });

  it("matches pages but not the BFF endpoints, Next internals or the login page", () => {
    const [matcher] = config.matcher;
    const regex = new RegExp(`^${matcher}$`);
    for (const path of ["/", "/dashboard", "/campaigns/1", "/loginx"]) {
      expect(regex.test(path), path).toBe(true);
    }
    for (const path of [
      "/api/auth/login",
      "/_next/static/a.js",
      "/login",
      "/login/",
      "/favicon.ico",
    ]) {
      expect(regex.test(path), path).toBe(false);
    }
  });
});
