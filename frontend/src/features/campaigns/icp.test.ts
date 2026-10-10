import { describe, expect, it } from "vitest";

import { makeCampaign } from "@/test/fixtures";

import { formatCompanySize, formatList, summarizeIcp } from "./icp";

describe("formatList", () => {
  it("says Any for nothing, lists a few and counts the rest", () => {
    expect(formatList([])).toBe("Any");
    expect(formatList(["DE", "AT"])).toBe("DE, AT");
    expect(formatList(["a", "b", "c", "d", "e"], 3)).toBe("a, b, c +2");
  });
});

describe("formatCompanySize", () => {
  it("formats ranges, open ends and no limit", () => {
    expect(formatCompanySize(50, 500)).toBe("50–500 employees");
    expect(formatCompanySize(10, 10)).toBe("10 employees");
    expect(formatCompanySize(50, null)).toBe("50+ employees");
    expect(formatCompanySize(null, 200)).toBe("Up to 200 employees");
    expect(formatCompanySize(null, null)).toBe("Any size");
    expect(formatCompanySize(0, 5)).toBe("0–5 employees");
  });
});

describe("summarizeIcp", () => {
  it("summarises country, industries, size and profile version", () => {
    expect(summarizeIcp(makeCampaign())).toEqual({
      countries: "DE, AT, CH",
      industries: "Software, Fintech +1",
      size: "50–500 employees",
      version: "v3",
    });
  });
});
