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
