import { describe, expect, it } from "vitest";

import { BRIEF_EXAMPLE } from "./presets";
import { describeProfile } from "./summary";
import { emptyValues, type FormValues } from "./values";

const name = (code: string) => (code === "SA" ? "Saudi Arabia" : code);

function values(overrides: Partial<FormValues> = {}): FormValues {
  return { ...emptyValues("client-1"), ...overrides };
}

describe("describeProfile", () => {
  it("restates the brief example in plain words", () => {
    const { lines, gaps } = describeProfile(
      values({ name: "SkyLight KSA", ...BRIEF_EXAMPLE }),
      name,
    );
    expect(lines[0]).toBe(
      "Looking for B2B companies in Saudi Arabia in Financial Services, Accounting, SaaS and Technology, 10\u2013500 employees.",
    );
    expect(lines).toContain(
      "Buyers ranked in this order: VP BD, Head of BD, Commercial Director, Sales Director, CEO, Founder, Managing Director.",
    );
    expect(lines).toContain("Messages written in Arabic and English.");
    expect(gaps).toEqual([]);
  });

  it("lists what is missing and what the engine does about it", () => {
    const { lines, gaps } = describeProfile(values(), name);
    expect(lines).toEqual(["Looking for B2B companies."]);
    expect(gaps).toContain(
      "No offer yet: the engine cannot judge whether a company fits.",
    );
    expect(gaps).toContain("No industries: every industry counts.");
    expect(gaps).toContain("No company size: any size counts.");
  });

  it("describes a one sided size range and exclusions", () => {
    const { lines } = describeProfile(
      values({
        offer: "x",
        company_size_min: "50",
        excluded_industries: ["Gambling"],
        excluded_company_types: ["Government"],
      }),
      name,
    );
    expect(lines[0]).toBe("Looking for B2B companies, at least 50 employees.");
    expect(lines).toContain("Skipping Gambling and Government.");
  });
});
