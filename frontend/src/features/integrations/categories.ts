import type { ConnectionCategory } from "./api";

/** The nine integration categories (Brief section 17), in the order the page shows them. */
export const CATEGORIES: ReadonlyArray<{
  value: ConnectionCategory;
  label: string;
  description: string;
}> = [
  {
    value: "llm",
    label: "AI / LLM",
    description: "Language models that assess companies and draft messages.",
  },
  {
    value: "company_data",
    label: "Company data",
    description: "Company search, firmographics, headcount and signals.",
  },
  {
    value: "people_data",
    label: "People data",
    description: "Contacts and buyer roles at a company.",
  },
  {
    value: "email_enrichment",
    label: "Email enrichment",
    description: "Finds email addresses for approved contacts.",
  },
  {
    value: "email_verification",
    label: "Email verification",
    description: "Checks that an address can receive mail.",
  },
  {
    value: "crm",
    label: "CRM",
    description: "Where approved accounts and contacts are pushed.",
  },
  {
    value: "outreach",
    label: "Outreach",
    description: "Sending and tracking messages.",
  },
  {
    value: "storage",
    label: "Storage",
    description: "File and export storage.",
  },
  {
    value: "automation",
    label: "Automation",
    description: "Workflow and job automation.",
  },
];

export function categoryLabel(value: string): string {
  return CATEGORIES.find((item) => item.value === value)?.label ?? value;
}

/**
 * Provider keys suggested in the form. `fake` is the built-in offline provider (every category
 * has a test for it). Real keys are added here as their adapters land (issue #74); any other
 * key can still be typed.
 */
export const KNOWN_PROVIDERS: Record<ConnectionCategory, readonly string[]> = {
  llm: ["fake"],
  company_data: ["fake"],
  people_data: ["fake"],
  email_enrichment: ["fake"],
  email_verification: ["fake"],
  crm: ["fake"],
  outreach: ["fake"],
  storage: ["fake"],
  automation: ["fake"],
};

/**
 * Hints for providers we know: the credential names the form starts with and what the
 * non-secret config accepts. The seam for form generation from adapter metadata (the API has no
 * metadata endpoint yet); an unknown provider gets the generic `api_key` row.
 */
export interface ProviderHints {
  credentialNames: readonly string[];
  configHint?: string;
}

export const PROVIDER_HINTS: Record<string, ProviderHints> = {
  fake: {
    credentialNames: ["api_key"],
    configHint:
      'Optional. {"simulate": "auth_failed" | "rate_limited" | "error"} makes the fake provider fail its test.',
  },
};

export const DEFAULT_HINTS: ProviderHints = { credentialNames: ["api_key"] };

export function hintsFor(provider: string): ProviderHints {
  return PROVIDER_HINTS[provider] ?? DEFAULT_HINTS;
}
