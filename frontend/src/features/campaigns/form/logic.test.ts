import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";

import {
  COUNTRY_CODES,
  countryOptions,
  ISO_3166_ALPHA2,
  LANGUAGE_CODES,
} from "./reference";
import { mapApiError } from "./serverErrors";
import { orderedErrors, validate } from "./validation";
import {
  type Campaign,
  emptyValues,
  type FormValues,
  isDirty,
  toCreateBody,
  toPatchBody,
  valuesFromCampaign,
} from "./values";

function valid(): FormValues {
  return {
    ...emptyValues("client-1"),
    name: "SkyLight KSA",
    offer: "B2B outbound",
  };
}

describe("reference data", () => {
  it("mirrors the backend ISO list", () => {
    expect(ISO_3166_ALPHA2).toHaveLength(249);
    expect(new Set(ISO_3166_ALPHA2).size).toBe(249);
    for (const code of ["SA", "AE", "US", "ZW"]) {
      expect(COUNTRY_CODES.has(code)).toBe(true);
    }
    expect(COUNTRY_CODES.has("XX")).toBe(false);
    expect([...LANGUAGE_CODES]).toEqual(["en", "ar"]);
  });

  it("offers named, sorted options", () => {
    const options = countryOptions();
    expect(options).toHaveLength(249);
    expect(options.find((o) => o.value === "SA")?.label).toBe("Saudi Arabia");
    const labels = options.map((o) => o.label);
    expect(labels).toEqual(
      [...labels].sort((a, b) => a.localeCompare(b, "en")),
    );
  });
});

describe("validate", () => {
  it("accepts a minimal valid form", () => {
    expect(validate(valid())).toEqual({});
  });

  it("requires client (create only), name and offer", () => {
    const errors = validate(emptyValues());
    expect(Object.keys(errors)).toEqual(["client", "name", "offer"]);
    expect(validate(emptyValues(), "edit").client).toBeUndefined();
    expect(validate({ ...valid(), offer: "   " }).offer).toBe(
      "Describe the offer.",
    );
  });

  it("checks company size", () => {
    expect(
      validate({ ...valid(), company_size_min: "500", company_size_max: "10" })
        .company_size_max,
    ).toMatch(/at least the minimum/);
    expect(
      validate({ ...valid(), company_size_min: "10", company_size_max: "10" }),
    ).toEqual({});
    expect(
      validate({ ...valid(), company_size_min: "-1" }).company_size_min,
    ).toMatch(/whole number/);
    expect(
      validate({ ...valid(), company_size_max: "1.5" }).company_size_max,
    ).toMatch(/whole number/);
    expect(
      validate({ ...valid(), company_size_max: "99999999999" })
        .company_size_max,
    ).toMatch(/too large/);
    expect(validate({ ...valid(), company_size_min: "5" })).toEqual({});
  });

  it("checks limits, countries and languages", () => {
    expect(validate({ ...valid(), countries: ["XX"] }).countries).toMatch(/XX/);
    expect(
      validate({ ...valid(), outreach_languages: ["fr"] }).outreach_languages,
    ).toMatch(/fr/);
    expect(
      validate({ ...valid(), industries: ["x".repeat(201)] }).industries,
    ).toMatch(/200/);
    expect(
      validate({
        ...valid(),
        industries: Array.from({ length: 101 }, (_, i) => `i${i}`),
      }).industries,
    ).toMatch(/100/);
    expect(
      validate({ ...valid(), custom_rules: "x".repeat(10001) }).custom_rules,
    ).toMatch(/10000/);
    expect(validate({ ...valid(), name: "x".repeat(201) }).name).toMatch(/200/);
  });

  it("lists errors in page order", () => {
    const errors = validate({
      ...emptyValues(),
      custom_rules: "x".repeat(10001),
    });
    expect(orderedErrors(errors).map((e) => e.field)).toEqual([
      "client",
      "name",
      "offer",
      "custom_rules",
    ]);
  });
});

describe("request bodies", () => {
  it("builds the create body with trimmed text and numeric sizes", () => {
    const body = toCreateBody({
      ...valid(),
      name: "  SkyLight  ",
      company_size_min: "10",
      company_size_max: "",
      countries: ["SA"],
      preferred_buyer_titles: ["CEO", "Founder"],
      outreach_languages: ["ar", "en"],
    });
    expect(body).toMatchObject({
      client: "client-1",
      name: "SkyLight",
      profile: {
        offer: "B2B outbound",
        countries: ["SA"],
        company_size_min: 10,
        company_size_max: null,
        business_model: "b2b",
        preferred_buyer_titles: ["CEO", "Founder"],
        outreach_languages: ["ar", "en"],
      },
    });
    expect(body.profile).not.toHaveProperty("change_note");
    expect(body.profile).not.toHaveProperty("structured_rules");
  });

  it("sends only changed rule fields on edit", () => {
    const original = {
      ...valid(),
      industries: ["SaaS"],
      preferred_buyer_titles: ["CEO", "Founder"],
    };
    expect(toPatchBody(original, { ...original })).toBeNull();
    expect(toPatchBody(original, { ...original, name: "New" })).toEqual({
      name: "New",
    });
    expect(
      toPatchBody(original, {
        ...original,
        preferred_buyer_titles: ["Founder", "CEO"],
        company_size_max: "50",
        change_note: " reordered ",
      }),
    ).toEqual({
      profile: {
        preferred_buyer_titles: ["Founder", "CEO"],
        company_size_max: 50,
        change_note: "reordered",
      },
    });
  });

  it("clears a field by sending an empty value", () => {
    const original = { ...valid(), company_size_min: "5", industries: ["A"] };
    expect(
      toPatchBody(original, {
        ...original,
        company_size_min: "",
        industries: [],
      }),
    ).toEqual({ profile: { company_size_min: null, industries: [] } });
  });

  it("a change note alone is still sent (the API decides)", () => {
    expect(toPatchBody(valid(), { ...valid(), change_note: "why" })).toEqual({
      profile: { change_note: "why" },
    });
  });

  it("round-trips a campaign into form values", () => {
    const campaign = {
      id: "c1",
      client: "client-1",
      name: "N",
      status: "draft",
      profile_version: 3,
      profile: {
        offer: "O",
        countries: ["SA"],
        industries: [],
        company_size_min: 10,
        company_size_max: null,
        business_model: "both",
        excluded_industries: [],
        excluded_company_types: [],
        target_departments: ["Sales"],
        preferred_buyer_titles: ["CEO"],
        outreach_languages: ["en"],
        custom_rules: "r",
        change_note: "old note",
      },
    } as unknown as Campaign;
    const values = valuesFromCampaign(campaign);
    expect(values).toMatchObject({
      client: "client-1",
      company_size_min: "10",
      company_size_max: "",
      business_model: "both",
      change_note: "",
    });
    expect(isDirty(values, { ...values })).toBe(false);
    expect(isDirty(values, { ...values, offer: "x" })).toBe(true);
  });
});

describe("mapApiError", () => {
  function validation(details: unknown) {
    return new ApiError({
      status: 400,
      code: "validation_error",
      message: "Invalid input.",
      details,
    });
  }

  it("maps top-level and nested profile errors to the same-named fields", () => {
    const mapped = mapApiError(
      validation({
        name: ["A campaign with this name already exists."],
        profile: {
          offer: ["This field may not be blank."],
          company_size_max: [
            "Maximum company size must be at least the minimum.",
          ],
          countries: ["Unknown country code(s): XX."],
        },
      }),
    );
    expect(mapped.fields).toEqual({
      name: "A campaign with this name already exists.",
      offer: "This field may not be blank.",
      company_size_max: "Maximum company size must be at least the minimum.",
      countries: "Unknown country code(s): XX.",
    });
    expect(mapped.other).toEqual([]);
  });

  it("flattens list item errors", () => {
    const mapped = mapApiError(
      validation({
        profile: { industries: { "0": ["Too long."], "2": ["Blank."] } },
      }),
    );
    expect(mapped.fields.industries).toBe("Too long. Blank.");
  });

  it("keeps unsupported fields and non-field errors visible", () => {
    const mapped = mapApiError(
      validation({
        profile: {
          structured_rules: ["Structured rules are not supported yet."],
        },
        non_field_errors: ["Archived campaigns are read-only."],
      }),
    );
    expect(mapped.fields).toEqual({});
    expect(mapped.other).toEqual([
      "structured_rules: Structured rules are not supported yet.",
      "Archived campaigns are read-only.",
    ]);
  });

  it("falls back to the message for other errors", () => {
    const mapped = mapApiError(
      new ApiError({ status: 500, code: "internal_error", message: "Boom" }),
    );
    expect(mapped).toEqual({ fields: {}, other: ["Boom"] });
  });
});
