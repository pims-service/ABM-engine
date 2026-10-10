import type { Campaign } from "@/features/campaigns/api";
import type { Client } from "@/features/clients/api";

/** Test data builders for the client and campaign API shapes. */

export const CLIENT_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
export const CLIENT_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
export const CAMPAIGN_1 = "c1c1c1c1-c1c1-4c1c-8c1c-c1c1c1c1c1c1";
export const CAMPAIGN_2 = "c2c2c2c2-c2c2-4c2c-8c2c-c2c2c2c2c2c2";

export function makeClient(overrides: Partial<Client> = {}): Client {
  return {
    id: CLIENT_A,
    name: "Acme Corp",
    notes: "",
    status: "active",
    archived_at: null,
    created_at: "2026-01-02T10:00:00Z",
    updated_at: "2026-02-03T10:00:00Z",
    ...overrides,
  };
}

export function makeCampaign(overrides: Partial<Campaign> = {}): Campaign {
  const id = overrides.id ?? CAMPAIGN_1;
  return {
    id,
    client: CLIENT_A,
    name: "DACH SaaS",
    status: "active",
    archived_at: null,
    profile_version: 3,
    profile: {
      id: "d0d0d0d0-d0d0-4d0d-8d0d-d0d0d0d0d0d0",
      campaign: id,
      version: 3,
      offer: "Payroll software",
      countries: ["DE", "AT", "CH"],
      industries: ["Software", "Fintech", "Logistics"],
      company_size_min: 50,
      company_size_max: 500,
      business_model: "b2b",
      excluded_industries: [],
      excluded_company_types: [],
      target_departments: [],
      preferred_buyer_titles: [],
      outreach_languages: [],
      custom_rules: "",
      change_note: "",
      created_by: null,
      created_at: "2026-01-02T10:00:00Z",
    },
    created_at: "2026-01-02T10:00:00Z",
    updated_at: "2026-02-03T10:00:00Z",
    ...overrides,
  };
}

export function pageOf<T>(results: T[], count = results.length) {
  return { count, next: null, previous: null, results };
}
