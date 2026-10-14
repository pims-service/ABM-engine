/**
 * One-click suggestions for the campaign form. They come from the examples in the V1 brief
 * (section 3) and are only shortcuts: any text can still be typed, and the API accepts the same
 * values either way.
 */
import type { FormValues } from "./values";

export const INDUSTRY_SUGGESTIONS = [
  "Financial Services",
  "Accounting",
  "SaaS",
  "Technology",
  "Logistics",
  "Retail",
  "Healthcare",
  "Real Estate",
  "Manufacturing",
  "Professional Services",
] as const;

export const DEPARTMENT_SUGGESTIONS = [
  "Sales",
  "Business Development",
  "Commercial",
  "Partnerships",
  "Marketing",
] as const;

export const BUYER_TITLE_SUGGESTIONS = [
  "VP BD",
  "Head of BD",
  "Commercial Director",
  "Sales Director",
  "CEO",
  "Founder",
  "Managing Director",
] as const;

export const EXCLUDED_TYPE_SUGGESTIONS = [
  "Government",
  "Non-profit",
  "Agency",
  "Freelancer",
  "Educational institution",
] as const;

/** The worked example of the brief (client SkyLight), without a client or a campaign name. */
export const BRIEF_EXAMPLE: Partial<FormValues> = {
  offer: "B2B outbound / lead generation",
  countries: ["SA"],
  industries: ["Financial Services", "Accounting", "SaaS", "Technology"],
  company_size_min: "10",
  company_size_max: "500",
  business_model: "b2b",
  target_departments: [
    "Sales",
    "Business Development",
    "Commercial",
    "Partnerships",
  ],
  preferred_buyer_titles: [
    "VP BD",
    "Head of BD",
    "Commercial Director",
    "Sales Director",
    "CEO",
    "Founder",
    "Managing Director",
  ],
  outreach_languages: ["ar", "en"],
};
