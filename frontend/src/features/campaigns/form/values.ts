import type { components } from "@/lib/api";

import type { BusinessModel } from "./reference";

export type Campaign = components["schemas"]["Campaign"];
export type CampaignProfile = components["schemas"]["CampaignProfile"];
export type CampaignWriteRequest =
  components["schemas"]["CampaignWriteRequest"];
export type CampaignPatchRequest =
  components["schemas"]["PatchedCampaignWriteRequest"];
export type ProfileInput = components["schemas"]["CampaignProfileInputRequest"];

/** Rule fields that hold a list of strings. */
export const LIST_FIELDS = [
  "countries",
  "industries",
  "excluded_industries",
  "excluded_company_types",
  "target_departments",
  "preferred_buyer_titles",
  "outreach_languages",
] as const;

export type ListField = (typeof LIST_FIELDS)[number];

/** Form state. Field names are the API's, so errors map one to one. */
export interface FormValues {
  client: string;
  name: string;
  offer: string;
  countries: string[];
  industries: string[];
  /** Kept as text while editing; "" means not set. */
  company_size_min: string;
  company_size_max: string;
  business_model: BusinessModel;
  excluded_industries: string[];
  excluded_company_types: string[];
  target_departments: string[];
  preferred_buyer_titles: string[];
  outreach_languages: string[];
  custom_rules: string;
  /** Why this version is made. Only sent when filled in. */
  change_note: string;
}

export type FieldKey = keyof FormValues;

export function emptyValues(client = ""): FormValues {
  return {
    client,
    name: "",
    offer: "",
    countries: [],
    industries: [],
    company_size_min: "",
    company_size_max: "",
    business_model: "b2b",
    excluded_industries: [],
    excluded_company_types: [],
    target_departments: [],
    preferred_buyer_titles: [],
    outreach_languages: [],
    custom_rules: "",
    change_note: "",
  };
}

/** Form values for editing: the campaign's current profile. The change note starts empty. */
export function valuesFromCampaign(campaign: Campaign): FormValues {
  const p = campaign.profile;
  return {
    client: campaign.client,
    name: campaign.name,
    offer: p.offer,
    countries: [...p.countries],
    industries: [...p.industries],
    company_size_min:
      p.company_size_min == null ? "" : String(p.company_size_min),
    company_size_max:
      p.company_size_max == null ? "" : String(p.company_size_max),
    business_model: p.business_model,
    excluded_industries: [...p.excluded_industries],
    excluded_company_types: [...p.excluded_company_types],
    target_departments: [...p.target_departments],
    preferred_buyer_titles: [...p.preferred_buyer_titles],
    outreach_languages: [...p.outreach_languages],
    custom_rules: p.custom_rules,
    change_note: "",
  };
}

function sizeOrNull(text: string): number | null {
  const trimmed = text.trim();
  return trimmed === "" ? null : Number(trimmed);
}

/** The rule fields as the API takes them (trimmed, sizes as numbers or null). */
function profileFromValues(
  values: FormValues,
): Required<Omit<ProfileInput, "change_note">> {
  return {
    offer: values.offer.trim(),
    countries: values.countries,
    industries: values.industries,
    company_size_min: sizeOrNull(values.company_size_min),
    company_size_max: sizeOrNull(values.company_size_max),
    business_model: values.business_model,
    excluded_industries: values.excluded_industries,
    excluded_company_types: values.excluded_company_types,
    target_departments: values.target_departments,
    preferred_buyer_titles: values.preferred_buyer_titles,
    outreach_languages: values.outreach_languages,
    custom_rules: values.custom_rules.trim(),
  };
}

/** Body of `POST /campaigns/`: everything. */
export function toCreateBody(values: FormValues): CampaignWriteRequest {
  const profile: ProfileInput = profileFromValues(values);
  const note = values.change_note.trim();
  if (note) profile.change_note = note;
  return { client: values.client, name: values.name.trim(), profile };
}

function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/**
 * Body of `PATCH /campaigns/{id}/`: only what differs from `original`, so an edit never
 * re-sends (and never accidentally clears) the fields the person did not touch. `profile` is left
 * out when no rule changed and no change note was written. Returns null when nothing differs.
 */
export function toPatchBody(
  original: FormValues,
  current: FormValues,
): CampaignPatchRequest | null {
  const body: CampaignPatchRequest = {};
  const name = current.name.trim();
  if (name !== original.name.trim()) body.name = name;

  const before = profileFromValues(original) as Record<string, unknown>;
  const after = profileFromValues(current) as Record<string, unknown>;
  const profile: Record<string, unknown> = {};
  for (const key of Object.keys(after)) {
    if (!same(before[key], after[key])) profile[key] = after[key];
  }
  const note = current.change_note.trim();
  if (note) profile.change_note = note;
  if (Object.keys(profile).length) body.profile = profile as ProfileInput;

  return Object.keys(body).length ? body : null;
}

/** True when any value differs from the saved ones (text compared as typed, so whitespace counts). */
export function isDirty(saved: FormValues, current: FormValues): boolean {
  return !same(saved, current);
}
