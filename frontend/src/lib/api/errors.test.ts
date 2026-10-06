import { describe, expect, it } from "vitest";

import { ApiError, isErrorEnvelope } from "./errors";

describe("isErrorEnvelope", () => {
  it("accepts the standard envelope", () => {
    expect(
      isErrorEnvelope({
        error: { code: "x", message: "m", details: null, request_id: null },
      }),
    ).toBe(true);
  });

  it.each([
    null,
    undefined,
    "x",
    1,
    {},
    { error: null },
    { error: { code: 1, message: "m" } },
  ])("rejects %j", (value) => {
    expect(isErrorEnvelope(value)).toBe(false);
  });
});

describe("ApiError", () => {
  it("is an Error with typed fields and defaults", () => {
    const error = new ApiError({
      status: 500,
      code: "internal_error",
      message: "boom",
    });
    expect(error).toBeInstanceOf(Error);
    expect(error).toMatchObject({
      name: "ApiError",
      details: null,
      requestId: null,
    });
    expect(error.fieldErrors).toEqual({});
    expect(error.retryAfter).toBeNull();
    expect(error.isUnauthorized).toBe(false);
  });

  it("only reads field errors from validation_error details", () => {
    const details = { name: ["Required."], odd: "not a list" };
    expect(
      new ApiError({
        status: 400,
        code: "validation_error",
        message: "m",
        details,
      }).fieldErrors,
    ).toEqual({ name: ["Required."] });
    expect(
      new ApiError({ status: 400, code: "other", message: "m", details })
        .fieldErrors,
    ).toEqual({});
    expect(
      new ApiError({
        status: 400,
        code: "validation_error",
        message: "m",
        details: ["x"],
      }).fieldErrors,
    ).toEqual({});
  });

  it("reads the envelope and falls back to the X-Request-ID header", async () => {
    const response = new Response(
      JSON.stringify({
        error: {
          code: "not_found",
          message: "Not found.",
          details: null,
          request_id: null,
        },
      }),
      { status: 404, headers: { "X-Request-ID": "from-header" } },
    );
    const error = await ApiError.fromResponse(response);
    expect(error).toMatchObject({
      status: 404,
      code: "not_found",
      requestId: "from-header",
    });
  });

  it("handles an empty body", async () => {
    const error = await ApiError.fromResponse(
      new Response(null, { status: 500 }),
    );
    expect(error).toMatchObject({
      status: 500,
      code: "http_error",
      message: "HTTP 500",
    });
  });
});
