import { describe, expect, it } from "vitest";

import { isPublicPath, loginUrl, safeNextPath } from "./redirect";

describe("safeNextPath", () => {
  it.each([
    ["/campaigns", "/campaigns"],
    ["/campaigns/42?tab=notes#top", "/campaigns/42?tab=notes#top"],
    ["/companies?status=active&page=2", "/companies?status=active&page=2"],
  ])("keeps the same-origin path %s", (input, expected) => {
    expect(safeNextPath(input)).toBe(expected);
  });

  it.each([
    "https://evil.example/steal",
    "http://evil.example",
    "//evil.example",
    "///evil.example",
    "/\\evil.example",
    "\\\\evil.example",
    "/\\/evil.example",
    "/%2F/evil.example",
    "/%5Cevil.example",
    "javascript:alert(1)",
    "evil.example",
    "dashboard",
    "/ok\nSet-Cookie: a=b",
    "/ok\r\n",
    "/\tevil",
    "",
  ])("rejects %j", (input) => {
    expect(safeNextPath(input)).toBe("/dashboard");
  });

  it("falls back for missing values and uses a custom fallback", () => {
    expect(safeNextPath(undefined)).toBe("/dashboard");
    expect(safeNextPath(null)).toBe("/dashboard");
    expect(safeNextPath("//x", "/home")).toBe("/home");
  });

  it("does not bounce back to login or into the BFF endpoints", () => {
    expect(safeNextPath("/login")).toBe("/dashboard");
    expect(safeNextPath("/login?next=/x")).toBe("/dashboard");
    expect(safeNextPath("/api/auth/logout")).toBe("/dashboard");
  });

  it("rejects absurdly long values", () => {
    expect(safeNextPath(`/${"a".repeat(3000)}`)).toBe("/dashboard");
  });
});

describe("loginUrl", () => {
  it("encodes the page to return to", () => {
    expect(loginUrl("/campaigns?status=active")).toBe(
      "/login?next=%2Fcampaigns%3Fstatus%3Dactive",
    );
  });

  it("marks expired sessions", () => {
    expect(loginUrl("/companies", { expired: true })).toBe(
      "/login?next=%2Fcompanies&reason=expired",
    );
  });

  it("drops an unsafe return path instead of encoding it", () => {
    expect(loginUrl("//evil.example")).toBe("/login");
    expect(loginUrl(undefined)).toBe("/login");
  });
});

describe("isPublicPath", () => {
  it("only treats the login page as public", () => {
    expect(isPublicPath("/login")).toBe(true);
    expect(isPublicPath("/login/")).toBe(true);
    expect(isPublicPath("/loginx")).toBe(false);
    expect(isPublicPath("/dashboard")).toBe(false);
  });
});
