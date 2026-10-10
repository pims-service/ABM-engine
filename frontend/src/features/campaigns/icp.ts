import type { Campaign } from "./api";

/** "US, GB, DE +2": the first `max` items and how many more there are; "Any" when empty. */
export function formatList(items: readonly string[], max = 3): string {
  if (items.length === 0) return "Any";
  const shown = items.slice(0, max).join(", ");
  const more = items.length - max;
  return more > 0 ? `${shown} +${more}` : shown;
}

/** "50-500 employees", "50+ employees", "Up to 500 employees" or "Any size". */
export function formatCompanySize(
  min: number | null | undefined,
  max: number | null | undefined,
): string {
  const hasMin = typeof min === "number";
  const hasMax = typeof max === "number";
  if (hasMin && hasMax) {
    return min === max ? `${min} employees` : `${min}–${max} employees`;
  }
  if (hasMin) return `${min}+ employees`;
  if (hasMax) return `Up to ${max} employees`;
  return "Any size";
}

export interface IcpSummary {
  countries: string;
  industries: string;
  size: string;
  /** For example "v3". */
  version: string;
}

/** The compact ICP summary the campaign list shows for one campaign. */
export function summarizeIcp(campaign: Campaign): IcpSummary {
  const { profile } = campaign;
  return {
    countries: formatList(profile.countries, 4),
    industries: formatList(profile.industries, 2),
    size: formatCompanySize(profile.company_size_min, profile.company_size_max),
    version: `v${campaign.profile_version}`,
  };
}
