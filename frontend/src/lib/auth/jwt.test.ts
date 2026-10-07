import { describe, expect, it } from "vitest";

import { jwtExpiry } from "./jwt";

function token(payload: unknown): string {
  const encode = (value: unknown) =>
    Buffer.from(JSON.stringify(value)).toString("base64url");
  return `${encode({ alg: "none" })}.${encode(payload)}.sig`;
}

describe("jwtExpiry", () => {
  it("reads exp in seconds", () => {
    expect(jwtExpiry(token({ exp: 1_900_000_000 }))).toBe(1_900_000_000);
  });

  it.each([
    ["no exp", token({ sub: "1" })],
    ["non-numeric exp", token({ exp: "soon" })],
    ["not a JWT", "nope"],
    ["garbage payload", "a.%%%.c"],
    ["empty", ""],
  ])("returns null for %s", (_name, value) => {
    expect(jwtExpiry(value)).toBeNull();
  });
});
