/**
 * Companies API: typed fetch helpers. All helpers reject with `ApiError` (see `@/lib/api`).
 */
import { type components, getApiClient } from "@/lib/api";

export type Company = components["schemas"]["Company"];
export type CompanyDetail = components["schemas"]["CompanyDetail"];
export type WebsiteResearch = components["schemas"]["WebsiteResearch"];
export type ResearchedPage = components["schemas"]["ResearchedPage"];

export async function getCompany(
  id: string,
  signal?: AbortSignal,
): Promise<CompanyDetail> {
  const { data } = await getApiClient().GET("/api/v1/companies/{id}/", {
    params: { path: { id } },
    signal,
  });
  return data as CompanyDetail;
}

/** Add one company by hand (EDIT level). Returns the created (or already existing) company. */
export async function addCompany(
  campaignId: string,
  input: { name: string; website: string },
): Promise<void> {
  await getApiClient().POST("/api/v1/campaigns/{campaign_pk}/companies/", {
    params: { path: { campaign_pk: campaignId } },
    body: {
      name: input.name,
      website: input.website || undefined,
      analyze: false,
    },
  });
}

/** Read the company's public website now. Can take up to about a minute. */
export async function researchWebsite(
  id: string,
  signal?: AbortSignal,
): Promise<WebsiteResearch> {
  const { data } = await getApiClient().POST(
    "/api/v1/companies/{id}/website-research/",
    { params: { path: { id } }, signal },
  );
  return data as WebsiteResearch;
}
