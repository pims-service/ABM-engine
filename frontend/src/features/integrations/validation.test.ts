import { describe, expect, it } from "vitest";

import {
  configToText,
  credentialsBody,
  parseConfig,
  validateCredentialRows,
  validateName,
  validateProvider,
} from "./validation";

describe("validateProvider", () => {
  it("accepts slugs", () => {
    expect(validateProvider("fake")).toBeUndefined();
    expect(validateProvider("people_data_labs2")).toBeUndefined();
  });

  it.each(["", "Fake", "1abc", "my-provider", "a b", "x".repeat(65)])(
    "rejects %j",
    (value) => {
      expect(validateProvider(value)).toBeTruthy();
    },
  );
});

describe("validateName", () => {
  it("requires a non-blank name of at most 200 characters", () => {
    expect(validateName("  ")).toBeTruthy();
    expect(validateName("x".repeat(201))).toBeTruthy();
    expect(validateName(" Primary ")).toBeUndefined();
  });
});

describe("parseConfig", () => {
  it("treats empty text as no settings", () => {
    expect(parseConfig("  ")).toEqual({ ok: true, value: {} });
  });

  it("parses a JSON object", () => {
    expect(parseConfig('{"model": "fast", "n": 2}')).toEqual({
      ok: true,
      value: { model: "fast", n: 2 },
    });
  });

  it.each(["{oops", "[1,2]", "null", '"text"', "42"])("rejects %s", (text) => {
    const result = parseConfig(text);
    expect(result.ok).toBe(false);
  });

  it("refuses secret-looking keys at any depth", () => {
    const result = parseConfig('{"a": {"api_key": "x"}}');
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toContain("api_key");
  });

  it("round-trips through configToText", () => {
    expect(configToText({})).toBe("");
    expect(configToText(null)).toBe("");
    expect(parseConfig(configToText({ a: 1 }))).toEqual({
      ok: true,
      value: { a: 1 },
    });
  });
});

describe("credential rows", () => {
  it("needs at least one row with a valid, unique name and a value", () => {
    expect(validateCredentialRows([]).form).toBeTruthy();
    const checked = validateCredentialRows([
      { id: 1, name: "api_key", value: "" },
      { id: 2, name: "api_key", value: "x" },
      { id: 3, name: "9bad", value: "x" },
    ]);
    expect(checked.rows[1]?.value).toBeTruthy();
    expect(checked.rows[2]?.name).toBeTruthy();
    expect(checked.rows[3]?.name).toBeTruthy();
  });

  it("never echoes a value in an error message", () => {
    const secret = "YOUR_FAKE_SECRET_VALUE_1234"; // pragma: allowlist secret
    const checked = validateCredentialRows([
      { id: 1, name: "9bad", value: secret },
    ]);
    expect(JSON.stringify(checked)).not.toContain(secret);
  });

  it("builds the request body with trimmed names and values", () => {
    expect(
      credentialsBody([{ id: 1, name: " api_key ", value: " v " }]),
    ).toEqual({ api_key: "v" });
  });
});
