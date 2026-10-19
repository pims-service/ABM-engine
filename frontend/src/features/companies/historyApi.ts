/** The research snapshots of a company and their changes. Rejects with `ApiError`. */
import { type components, getApiClient } from "@/lib/api";

export type Snapshot = components["schemas"]["Snapshot"];

export async function getResearchHistory(
  companyId: string,
  signal?: AbortSignal,
): Promise<Snapshot[]> {
  const { data } = await getApiClient().GET(
    "/api/v1/companies/{id}/research-history/",
    { params: { path: { id: companyId } }, signal },
  );
  return data as Snapshot[];
}
