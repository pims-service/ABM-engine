/**
 * The explainable ranking of the people at a company (Brief sections 8 and 15).
 * All helpers reject with `ApiError`.
 */
import { type components, getApiClient } from "@/lib/api";

export type BuyerRanking = components["schemas"]["BuyerRanking"];

/** The suggested order with reasons. Nothing is stored. */
export async function getRanking(
  companyId: string,
  signal?: AbortSignal,
): Promise<BuyerRanking> {
  const { data } = await getApiClient().GET(
    "/api/v1/companies/{id}/buyer-ranking/",
    { params: { path: { id: companyId } }, signal },
  );
  return data as BuyerRanking;
}

/** Save the ranks and the primary and secondary targets (manager level). */
export async function applyRanking(companyId: string): Promise<BuyerRanking> {
  const { data } = await getApiClient().POST(
    "/api/v1/companies/{id}/apply-buyer-ranking/",
    { params: { path: { id: companyId } } },
  );
  return data as BuyerRanking;
}
