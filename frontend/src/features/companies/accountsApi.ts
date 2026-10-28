/**
 * The master ABM dashboard API (Brief section 13). All helpers reject with `ApiError`.
 */
import { type components, getApiClient } from "@/lib/api";

export type AccountRow = components["schemas"]["AccountRow"];
export type AccountSummary = components["schemas"]["AccountSummary"];
export type AccountPage = components["schemas"]["PaginatedAccountRowList"];
export type BulkResult = components["schemas"]["BulkAnalyzeResult"];

export interface AccountFilters {
  search: string;
  icp_fit: string;
  trigger: string;
  ai: string;
  review: string;
  industry: string;
  human: string;
  outreach: string;
  country: string;
  size_min: string;
  size_max: string;
  trigger_type: string;
  buyer_title: string;
  ordering: string;
  page: number;
}

export const EMPTY_FILTERS: AccountFilters = {
  search: "",
  icp_fit: "",
  trigger: "",
  ai: "",
  review: "",
  industry: "",
  human: "",
  outreach: "",
  country: "",
  size_min: "",
  size_max: "",
  trigger_type: "",
  buyer_title: "",
  ordering: "",
  page: 1,
};

/** Only the filters that are set go into the query. */
function toQuery(filters: AccountFilters) {
  const query: Record<string, string | number> = { page: filters.page };
  for (const key of [
    "search",
    "icp_fit",
    "trigger",
    "ai",
    "review",
    "industry",
    "human",
    "outreach",
    "country",
    "size_min",
    "size_max",
    "trigger_type",
    "buyer_title",
    "ordering",
  ] as const) {
    if (filters[key]) query[key] = filters[key];
  }
  return query;
}

export async function listAccounts(
  campaignId: string,
  filters: AccountFilters,
  signal?: AbortSignal,
): Promise<AccountPage> {
  const { data } = await getApiClient().GET(
    "/api/v1/campaigns/{campaign_pk}/accounts/",
    {
      params: {
        path: { campaign_pk: campaignId },
        // The generated type lists each filter as a union of its allowed values.
        query: toQuery(filters) as never,
      },
      signal,
    },
  );
  return data as AccountPage;
}

export async function getAccountSummary(
  campaignId: string,
  signal?: AbortSignal,
): Promise<AccountSummary> {
  const { data } = await getApiClient().GET(
    "/api/v1/campaigns/{campaign_pk}/accounts/summary/",
    { params: { path: { campaign_pk: campaignId } }, signal },
  );
  return data as AccountSummary;
}

/** Queue the analysis of every company never analyzed (manager level). */
export async function analyzeUnassessed(
  campaignId: string,
): Promise<BulkResult> {
  const { data } = await getApiClient().POST(
    "/api/v1/campaigns/{campaign_pk}/accounts/analyze/",
    {
      params: { path: { campaign_pk: campaignId } },
      body: { scope: "unassessed" },
    },
  );
  return data as BulkResult;
}

export type EnrichResult = components["schemas"]["EnrichContactsResult"];

/**
 * Queue the email lookup and check for the primary and secondary people of every approved
 * account (manager level). Runs in the background; the answer only counts who was queued.
 */
export async function findAndVerifyEmails(
  campaignId: string,
): Promise<EnrichResult> {
  const { data } = await getApiClient().POST(
    "/api/v1/campaigns/{campaign_pk}/accounts/enrich-contacts/",
    {
      params: { path: { campaign_pk: campaignId } },
      body: { step: "find_and_verify" },
    },
  );
  return data as EnrichResult;
}

/** Download the approved accounts (those you decided ADD) as a CSV file (manager level). */
export async function exportApproved(campaignId: string): Promise<void> {
  const { data } = await getApiClient().GET(
    "/api/v1/campaigns/{campaign_pk}/accounts/export/",
    { params: { path: { campaign_pk: campaignId } }, parseAs: "text" },
  );
  const url = URL.createObjectURL(
    new Blob([String(data)], { type: "text/csv;charset=utf-8" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = "approved-accounts.csv";
  link.click();
  URL.revokeObjectURL(url);
}

const STAGE_LABEL: Record<string, string> = {
  none: "Not started",
  contacted: "Contacted",
  replied: "Replied",
  meeting_booked: "Meeting booked",
};

export function stageLabel(stage: string): string {
  return STAGE_LABEL[stage] ?? stage;
}

export type BulkDecisionResult = components["schemas"]["BulkDecisionResult"];

/** The ids of the accounts waiting for a decision (assessed, undecided), in dashboard order. */
export async function listReviewQueue(
  campaignId: string,
  signal?: AbortSignal,
): Promise<string[]> {
  const ids: string[] = [];
  for (let page = 1; page <= 20; page += 1) {
    const result = await listAccounts(
      campaignId,
      { ...EMPTY_FILTERS, review: "needs_review", page },
      signal,
    );
    ids.push(...result.results.map((row) => row.id));
    if (!result.next) break;
  }
  return ids;
}

/** One decision for many accounts; the answer says what happened to each (reviewer level). */
export async function decideMany(
  campaignId: string,
  companyIds: string[],
  decision: "add" | "hold" | "skip",
  note = "",
): Promise<BulkDecisionResult> {
  const { data } = await getApiClient().POST(
    "/api/v1/campaigns/{campaign_pk}/accounts/decide/",
    {
      params: { path: { campaign_pk: campaignId } },
      body: { company_ids: companyIds, decision, note },
    },
  );
  return data as BulkDecisionResult;
}
