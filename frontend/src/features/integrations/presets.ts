import type { ConnectionCategory } from "./api";

/** One input of a guided form. Secrets are sent as credentials, everything else as settings. */
export interface PresetField {
  /** The credential or settings name sent to the API. */
  name: string;
  label: string;
  kind: "secret" | "setting";
  required?: boolean;
  placeholder?: string;
  hint?: string;
  /** A fixed list of choices (settings only); the first option is the default. */
  options?: readonly { value: string; label: string }[];
}

export type ProviderGroup = "ai" | "data" | "people" | "crm";

/** The filter chips on the Integrations page. */
export const GROUPS: readonly { id: ProviderGroup; label: string }[] = [
  { id: "ai", label: "AI" },
  { id: "data", label: "Company data" },
  { id: "people", label: "People and email" },
  { id: "crm", label: "CRM" },
];

/** A provider the app has an adapter for, with everything its tile and connect form need. */
export interface ProviderPreset {
  /** The provider key stored on the connection. */
  key: string;
  label: string;
  /** One short line for the tile. */
  tagline: string;
  /** One short line above the form. */
  blurb: string;
  group: ProviderGroup;
  /**
   * The provider's logo, a local file under /public/integrations. A `mono` logo is a single-color
   * mark drawn in the text color (so it works on the dark theme); the others keep their colors.
   */
  logo?: { src: string; mono?: boolean };
  /** The connection categories this one key sets up (SignalHire does two). */
  categories: readonly ConnectionCategory[];
  /** Where to get the key, shown as a link under the form. */
  keyLink?: { href: string; text: string };
  fields: readonly PresetField[];
}

const API_KEY = (hint: string): PresetField => ({
  name: "api_key",
  label: "API key",
  kind: "secret",
  required: true,
  hint,
});

/** Every provider with an adapter. A connection to anything else shows as a custom tile. */
export const PROVIDERS: readonly ProviderPreset[] = [
  {
    key: "anthropic",
    logo: { src: "/integrations/anthropic.svg", mono: true },
    label: "Anthropic Claude",
    tagline: "Claude models judge fit and write messages",
    blurb: "Used to analyze companies and draft outreach.",
    group: "ai",
    categories: ["llm"],
    keyLink: {
      href: "https://console.anthropic.com/settings/keys",
      text: "Get a key in the Anthropic Console",
    },
    fields: [
      {
        name: "model",
        label: "Model",
        kind: "setting",
        required: true,
        placeholder: "claude-sonnet-5-5",
        hint: "The exact model id from your Anthropic account.",
      },
      API_KEY("Starts with sk-ant-. Stored encrypted, never shown again."),
    ],
  },
  {
    key: "gemini",
    logo: { src: "/integrations/gemini.svg", mono: true },
    label: "Google Gemini",
    tagline: "Gemini models judge fit and write messages",
    blurb: "Used to analyze companies and draft outreach.",
    group: "ai",
    categories: ["llm"],
    keyLink: {
      href: "https://aistudio.google.com/apikey",
      text: "Get a key in Google AI Studio",
    },
    fields: [
      {
        name: "model",
        label: "Model",
        kind: "setting",
        required: true,
        placeholder: "gemini-2.5-flash",
        hint: "The exact model id from Google AI Studio.",
      },
      API_KEY("Stored encrypted, never shown again."),
    ],
  },
  {
    key: "openai",
    label: "OpenAI",
    logo: { src: "/integrations/openai.svg", mono: true },
    tagline: "GPT models judge fit and write messages",
    blurb: "Used to analyze companies and draft outreach.",
    group: "ai",
    categories: ["llm"],
    keyLink: {
      href: "https://platform.openai.com/api-keys",
      text: "Get a key on the OpenAI platform",
    },
    fields: [
      {
        name: "model",
        label: "Model",
        kind: "setting",
        required: true,
        placeholder: "gpt-4o",
        hint: "The exact model id from your OpenAI account.",
      },
      API_KEY("Starts with sk-. Stored encrypted, never shown again."),
    ],
  },
  {
    key: "signalhire",
    logo: { src: "/integrations/signalhire.png" },
    label: "SignalHire",
    tagline: "Find people and their work emails",
    blurb:
      "One key finds people (free quota) and reveals emails (1 credit each).",
    group: "people",
    categories: ["people_data", "email_enrichment"],
    keyLink: {
      href: "https://www.signalhire.com/",
      text: "Find it under Integrations & API in SignalHire",
    },
    fields: [API_KEY("Your SignalHire API key.")],
  },
  {
    key: "hunter",
    label: "Hunter",
    logo: { src: "/integrations/hunter.png" },
    tagline: "Find a work email from a name and company",
    blurb:
      "Finds an approved person's email from their name and website. Free when nothing is found.",
    group: "people",
    categories: ["email_enrichment"],
    keyLink: {
      href: "https://hunter.io/api-keys",
      text: "Get a key in your Hunter account",
    },
    fields: [API_KEY("Your Hunter API key.")],
  },
  {
    key: "zerobounce",
    logo: { src: "/integrations/zerobounce.webp" },
    label: "ZeroBounce",
    tagline: "Check that an email can receive mail",
    blurb: "Verifies the email of an approved person before you write.",
    group: "people",
    categories: ["email_verification"],
    keyLink: {
      href: "https://www.zerobounce.net/members/apikey/",
      text: "Get a key on the ZeroBounce API page",
    },
    fields: [
      API_KEY("Your ZeroBounce API key."),
      {
        name: "region",
        label: "Server region",
        kind: "setting",
        hint: "The region your ZeroBounce account uses. Leave on default if unsure.",
        options: [
          { value: "default", label: "Default" },
          { value: "us", label: "United States" },
          { value: "eu", label: "Europe" },
        ],
      },
    ],
  },
  {
    key: "gohighlevel",
    logo: { src: "/integrations/gohighlevel.png" },
    label: "GoHighLevel",
    tagline: "Push approved accounts to your CRM",
    blurb: "Approved accounts and their contacts are pushed here.",
    group: "crm",
    categories: ["crm"],
    keyLink: {
      href: "https://marketplace.gohighlevel.com/docs/Authorization/PrivateIntegrationsToken",
      text: "How to create a Private Integration token",
    },
    fields: [
      {
        name: "location_id",
        label: "Location ID",
        kind: "setting",
        required: true,
        hint: "Your HighLevel sub-account (location) id.",
      },
      {
        name: "api_key",
        label: "Private Integration token",
        kind: "secret",
        required: true,
        hint: "Needs contact permissions. Stored encrypted, never shown again.",
      },
      {
        name: "tag",
        label: "Tag (optional)",
        kind: "setting",
        hint: "Added to every contact the engine pushes.",
      },
    ],
  },
];

/** A provider the project has researched but has no integration for yet. Shown, not connectable. */
export interface PlannedProvider {
  key: string;
  label: string;
  tagline: string;
  group: ProviderGroup;
  categories: readonly ConnectionCategory[];
  logo?: { src: string; mono?: boolean };
}

/**
 * From the project's provider research (docs/integrations/provider-shortlist.md). They have no
 * adapter, so a tile offers no Connect button: nobody can save a key that nothing would use.
 */
export const PLANNED: readonly PlannedProvider[] = [
  {
    key: "peopledatalabs",
    label: "People Data Labs",
    logo: { src: "/integrations/peopledatalabs.ico" },
    tagline: "Company size, headcount growth and funding",
    group: "data",
    categories: ["company_data"],
  },
  {
    key: "coresignal",
    label: "Coresignal",
    logo: { src: "/integrations/coresignal.png" },
    tagline: "Company data, headcount history and job posts",
    group: "data",
    categories: ["company_data"],
  },
  {
    key: "wathq",
    label: "Wathq",
    tagline: "Saudi commercial registry: legal name and status",
    group: "data",
    categories: ["company_data"],
  },
  {
    key: "hubspot",
    label: "HubSpot",
    logo: { src: "/integrations/hubspot.svg", mono: true },
    tagline: "Push approved accounts to HubSpot CRM",
    group: "crm",
    categories: ["crm"],
  },
  {
    key: "bouncer",
    label: "Bouncer",
    logo: { src: "/integrations/bouncer.png" },
    tagline: "Check that an email can receive mail",
    group: "people",
    categories: ["email_verification"],
  },
  {
    key: "kickbox",
    label: "Kickbox",
    logo: { src: "/integrations/kickbox.svg" },
    tagline: "Check that an email can receive mail",
    group: "people",
    categories: ["email_verification"],
  },
];

export function providerPreset(key: string): ProviderPreset | undefined {
  return PROVIDERS.find((provider) => provider.key === key);
}
