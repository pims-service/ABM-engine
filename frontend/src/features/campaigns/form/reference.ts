/**
 * Reference data for the campaign form. Mirrors `backend/apps/campaigns/reference.py`
 * (`ISO_3166_ALPHA2`) and `settings.OUTREACH_LANGUAGES`; keep them in step. The API stays the
 * authority and rejects anything else, this only lets the form offer valid choices.
 */

/** The 249 officially assigned ISO 3166-1 alpha-2 codes (same list as the backend). */
export const ISO_3166_ALPHA2 = `
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ
BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ
DE DJ DK DM DO DZ
EC EE EG EH ER ES ET
FI FJ FK FM FO FR
GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY
HK HM HN HR HT HU
ID IE IL IM IN IO IQ IR IS IT
JE JM JO JP
KE KG KH KI KM KN KP KR KW KY KZ
LA LB LC LI LK LR LS LT LU LV LY
MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ
NA NC NE NF NG NI NL NO NP NR NU NZ
OM
PA PE PF PG PH PK PL PM PN PR PS PT PW PY
QA
RE RO RS RU RW
SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ
TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ
UA UG UM US UY UZ
VA VC VE VG VI VN VU
WF WS
YE YT
ZA ZM ZW
`
  .split(/\s+/)
  .filter(Boolean) as readonly string[];

export const COUNTRY_CODES: ReadonlySet<string> = new Set(ISO_3166_ALPHA2);

export interface CountryOption {
  value: string;
  label: string;
}

/** English display name of a country code; falls back to the code where the runtime has none. */
export function countryName(code: string): string {
  try {
    return new Intl.DisplayNames(["en"], { type: "region" }).of(code) ?? code;
  } catch {
    return code;
  }
}

/** Options for the country picker, sorted by name. */
export function countryOptions(): CountryOption[] {
  const names = new Intl.DisplayNames(["en"], { type: "region" });
  return ISO_3166_ALPHA2.map((code) => {
    let label: string = code;
    try {
      label = names.of(code) ?? code;
    } catch {
      /* keep the code */
    }
    return { value: code, label };
  }).sort((a, b) => a.label.localeCompare(b.label, "en"));
}

/** Outreach languages the platform supports (`settings.OUTREACH_LANGUAGES`). */
export const OUTREACH_LANGUAGES = [
  { value: "en", label: "English" },
  { value: "ar", label: "Arabic" },
] as const;

export const LANGUAGE_CODES: ReadonlySet<string> = new Set(
  OUTREACH_LANGUAGES.map((l) => l.value),
);

export const BUSINESS_MODELS = [
  { value: "b2b", label: "B2B" },
  { value: "b2c", label: "B2C" },
  { value: "both", label: "B2B and B2C" },
] as const;

export type BusinessModel = (typeof BUSINESS_MODELS)[number]["value"];

/** Limits of the API (`CampaignProfileInputSerializer`). */
export const LIMITS = {
  name: 200,
  offer: 5000,
  item: 200,
  items: 100,
  customRules: 10000,
  changeNote: 1000,
  size: 2_147_483_647,
} as const;
