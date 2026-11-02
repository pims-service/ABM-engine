/**
 * Collecting provider data for a company and reading what it found: runs, evidence and the
 * merged profile (Brief sections 5, 14 and 19). All helpers reject with `ApiError`.
 */
import { type components, getApiClient } from "@/lib/api";

export type ResearchRun = components["schemas"]["ResearchRun"];
export type ResearchStep = components["schemas"]["ResearchStep"];
export type ResearchRequest = components["schemas"]["ResearchRequest"];
export type EvidenceRow = components["schemas"]["CompanyEvidence"];
export type ProfileField = components["schemas"]["ProfileField"];

/** Queue a collection run (manager level). Returns the run, or the one already in progress. */
export async function startResearch(
  companyId: string,
): Promise<ResearchRequest> {
  const { data } = await getApiClient().POST(
    "/api/v1/companies/{id}/research/",
    { params: { path: { id: companyId } } },
  );
  return data as ResearchRequest;
}

/** The latest runs, newest first. */
export async function getResearchRuns(
  companyId: string,
  signal?: AbortSignal,
): Promise<ResearchRun[]> {
  const { data } = await getApiClient().GET(
    "/api/v1/companies/{id}/research-runs/",
    { params: { path: { id: companyId } }, signal },
  );
  return data as ResearchRun[];
}

/** Every source behind what we know, with its age and what it backs. */
export async function getEvidence(
  companyId: string,
  signal?: AbortSignal,
): Promise<EvidenceRow[]> {
  const { data } = await getApiClient().GET(
    "/api/v1/companies/{id}/evidence/",
    { params: { path: { id: companyId } }, signal },
  );
  return data as EvidenceRow[];
}

/** One value per profile field across all snapshots, with its source and the alternatives. */
export async function getMergedProfile(
  companyId: string,
  signal?: AbortSignal,
): Promise<ProfileField[]> {
  const { data } = await getApiClient().GET("/api/v1/companies/{id}/profile/", {
    params: { path: { id: companyId } },
    signal,
  });
  return data as ProfileField[];
}
