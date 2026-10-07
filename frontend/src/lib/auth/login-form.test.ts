import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api/errors";

import { mapLoginError, validateLogin } from "./login-form";

// Placeholder only; never a real credential. pragma: allowlist secret
const PASSWORD = "placeholder-password"; // pragma: allowlist secret

function apiError(status: number, code: string, details?: unknown): ApiError {
  return new ApiError({ status, code, message: "server text", details });
}

describe("validateLogin", () => {
  it("accepts a normal email and any non-empty password", () => {
    expect(
      validateLogin({ email: " ada@example.com ", password: PASSWORD }),
    ).toEqual({});
    expect(validateLogin({ email: "a@b.co", password: "x" })).toEqual({});
  });

  it("requires both fields", () => {
    expect(validateLogin({ email: "", password: "" })).toEqual({
      email: "Enter your email address.",
      password: "Enter your password.", // pragma: allowlist secret
    });
    expect(validateLogin({ email: "   ", password: PASSWORD }).email).toBe(
      "Enter your email address.",
    );
  });

  it.each(["ada", "ada@", "@example.com", "ada@example", "a b@example.com"])(
    "rejects the malformed email %j",
    (email) => {
      expect(validateLogin({ email, password: PASSWORD }).email).toMatch(
        /valid email/,
      );
    },
  );
});

describe("mapLoginError", () => {
  it("maps bad credentials to one generic message", () => {
    for (const code of ["authentication_failed", "not_authenticated"]) {
      expect(mapLoginError(apiError(401, code))).toEqual({
        form: "Incorrect email or password.",
        fields: {},
      });
    }
  });

  it("maps validation errors onto their fields", () => {
    const failure = mapLoginError(
      apiError(400, "validation_error", {
        email: ["Enter a valid email address."],
        password: ["This field may not be blank."],
        other: ["ignored"],
      }),
    );
    expect(failure).toEqual({
      fields: {
        email: "Enter a valid email address.",
        password: "This field may not be blank.", // pragma: allowlist secret
      },
    });
  });

  it("falls back to a form message when validation has no known field", () => {
    expect(
      mapLoginError(apiError(400, "validation_error", { other: ["x"] })).form,
    ).toMatch(/Check your details/);
  });

  it("says how long to wait when throttled", () => {
    expect(
      mapLoginError(apiError(429, "throttled", { retry_after: 30 })).form,
    ).toBe("Too many sign-in attempts. Try again in 30 seconds.");
    expect(
      mapLoginError(apiError(429, "throttled", { retry_after: 1 })).form,
    ).toMatch(/1 second\./);
    expect(mapLoginError(apiError(429, "throttled")).form).toMatch(
      /Wait a moment/,
    );
  });

  it("maps network and server failures", () => {
    expect(mapLoginError(apiError(0, "network_error")).form).toMatch(
      /Could not reach the server/,
    );
    expect(mapLoginError(apiError(502, "upstream_unavailable")).form).toMatch(
      /having trouble/,
    );
    expect(mapLoginError(apiError(418, "teapot")).form).toMatch(
      /Could not sign you in/,
    );
  });

  it("never echoes server text and handles unknown errors", () => {
    expect(
      mapLoginError(apiError(401, "authentication_failed")).form,
    ).not.toContain("server text");
    expect(mapLoginError(new Error("boom"))).toEqual({
      form: "Something went wrong. Please try again.",
      fields: {},
    });
  });
});
