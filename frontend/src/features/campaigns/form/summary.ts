import { BUSINESS_MODELS } from "./reference";
import type { FormValues } from "./values";

export interface ProfileSummary {
  /** What the rules say, in plain sentences. */
  lines: string[];
  /** What is not set, and what the engine will therefore do. */
  gaps: string[];
}

function joinList(items: readonly string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

function sizeText(min: string, max: string): string {
  const lo = min.trim();
  const hi = max.trim();
  if (lo && hi) return `${lo}\u2013${hi} employees`;
  if (lo) return `at least ${lo} employees`;
  if (hi) return `up to ${hi} employees`;
  return "";
}

/**
 * The campaign rules as the engine will read them: plain sentences, plus a list of what is
 * missing. Pure and deterministic: it only restates what was entered, it infers nothing.
 */
export function describeProfile(
  values: FormValues,
  countryName: (code: string) => string,
): ProfileSummary {
  const lines: string[] = [];
  const gaps: string[] = [];

  const model =
    BUSINESS_MODELS.find((m) => m.value === values.business_model)?.label ??
    "B2B";
  const countries = values.countries.map(countryName);
  const size = sizeText(values.company_size_min, values.company_size_max);

  let who = `${model} companies`;
  if (countries.length) who += ` in ${joinList(countries)}`;
  if (values.industries.length) who += ` in ${joinList(values.industries)}`;
  if (size) who += `, ${size}`;
  lines.push(`Looking for ${who}.`);

  const excluded = [
    ...values.excluded_industries,
    ...values.excluded_company_types,
  ];
  if (excluded.length) lines.push(`Skipping ${joinList(excluded)}.`);

  if (values.target_departments.length) {
    lines.push(
      `Checking the strength of the ${joinList(values.target_departments)} team${
        values.target_departments.length === 1 ? "" : "s"
      }.`,
    );
  }
  if (values.preferred_buyer_titles.length) {
    lines.push(
      `Buyers ranked in this order: ${values.preferred_buyer_titles.join(", ")}.`,
    );
  }
  if (values.outreach_languages.length) {
    const names = values.outreach_languages.map((code) =>
      code === "ar" ? "Arabic" : code === "en" ? "English" : code,
    );
    lines.push(`Messages written in ${joinList(names)}.`);
  }
  if (values.custom_rules.trim()) {
    lines.push("Your own qualification notes are applied on top.");
  }

  if (!values.offer.trim()) {
    gaps.push("No offer yet: the engine cannot judge whether a company fits.");
  }
  if (!countries.length)
    gaps.push("No country: companies from anywhere count.");
  if (!values.industries.length) {
    gaps.push("No industries: every industry counts.");
  }
  if (!size) gaps.push("No company size: any size counts.");
  if (!values.target_departments.length) {
    gaps.push("No target departments: team strength is not judged.");
  }
  if (!values.preferred_buyer_titles.length) {
    gaps.push("No buyer titles: buyers are ranked without a preference.");
  }
  if (!values.outreach_languages.length) {
    gaps.push("No outreach language chosen.");
  }
  return { lines, gaps };
}
