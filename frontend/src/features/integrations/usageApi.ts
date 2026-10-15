/**
 * AI usage and monthly limits of a client's connections. All helpers reject with `ApiError`.
 */
import { type components, getApiClient } from "@/lib/api";

export type UsageRow = components["schemas"]["UsageRow"];
export type Budget = components["schemas"]["Budget"];

/** First day of the current month (UTC), as the API expects it. */
function monthStart(): string {
  const now = new Date();
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1))
    .toISOString()
    .slice(0, 10);
}

/** This month's calls, tokens and errors of a client, one row per provider (manager level). */
export async function getUsageThisMonth(
  clientId: string,
  signal?: AbortSignal,
): Promise<UsageRow[]> {
  const { data } = await getApiClient().GET(
    "/api/v1/integrations/usage/summary/",
    {
      params: { query: { client: clientId, date_from: monthStart() } },
      signal,
    },
  );
  return (data as components["schemas"]["UsageSummary"]).results;
}

export async function getBudget(
  connectionId: string,
  signal?: AbortSignal,
): Promise<Budget> {
  const { data } = await getApiClient().GET(
    "/api/v1/integrations/connections/{id}/budget/",
    { params: { path: { id: connectionId } }, signal },
  );
  return data as Budget;
}

/** Replace the monthly limit. `creditLimit` is in thousands of tokens; null removes the cap. */
export async function saveBudget(
  connectionId: string,
  input: {
    creditLimit: string | null;
    warnAtPercent: number;
    hardStop: boolean;
  },
): Promise<Budget> {
  const { data } = await getApiClient().PUT(
    "/api/v1/integrations/connections/{id}/budget/",
    {
      params: { path: { id: connectionId } },
      body: {
        credit_limit: input.creditLimit,
        warn_at_percent: input.warnAtPercent,
        hard_stop: input.hardStop,
      },
    },
  );
  return data as Budget;
}
