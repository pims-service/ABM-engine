/**
 * Campaigns API: typed fetch helpers and data hooks. Shared building blocks (the switcher, the
 * lists and the campaign form import from here), so keep the exports stable.
 *
 * All helpers reject with `ApiError` (see `@/lib/api`) and take an optional `AbortSignal`.
 * Create and edit (`POST /campaigns/`, `PUT|PATCH /campaigns/{id}/`) belong to the campaign form
 * (issue #49) and are not wrapped here.
 */
import { type components, getApiClient } from "@/lib/api";
import type { ListQuery } from "@/lib/api/list-query";
import { type ApiQueryState, useApiQuery } from "@/lib/hooks/useApiQuery";

export type Campaign = components["schemas"]["Campaign"];
export type CampaignProfile = components["schemas"]["CampaignProfile"];
export type CampaignStatus = components["schemas"]["CampaignStatusEnum"];
export type CampaignPage = components["schemas"]["PaginatedCampaignList"];
export type CampaignListQuery = ListQuery<CampaignStatus> & {
  /** Only campaigns of this client (ignored by {@link listClientCampaigns}). */
  client?: string;
};

export const CAMPAIGN_STATUSES: readonly CampaignStatus[] = [
  "draft",
  "active",
  "archived",
];

/** Route of the campaign edit page (built by issue #49). */
export function campaignEditPath(id: string): string {
  return `/campaigns/${id}/edit`;
}

/** Route of the "new campaign" page (built by issue #49); `clientId` preselects the client. */
export function campaignNewPath(clientId?: string): string {
  return clientId
    ? `/campaigns/new?client=${encodeURIComponent(clientId)}`
    : "/campaigns/new";
}

/** One page of campaigns across the clients you can see (each with its current profile). */
export async function listCampaigns(
  query: CampaignListQuery = {},
  signal?: AbortSignal,
): Promise<CampaignPage> {
  const { data } = await getApiClient().GET("/api/v1/campaigns/", {
    params: { query },
    signal,
  });
  return data as CampaignPage;
}

/** One page of one client's campaigns (`/clients/{id}/campaigns/`). */
export async function listClientCampaigns(
  clientId: string,
  query: Omit<CampaignListQuery, "client"> = {},
  signal?: AbortSignal,
): Promise<CampaignPage> {
  const { data } = await getApiClient().GET(
    "/api/v1/clients/{client_pk}/campaigns/",
    { params: { path: { client_pk: clientId }, query }, signal },
  );
  return data as CampaignPage;
}

export async function getCampaign(
  id: string,
  signal?: AbortSignal,
): Promise<Campaign> {
  const { data } = await getApiClient().GET("/api/v1/campaigns/{id}/", {
    params: { path: { id } },
    signal,
  });
  return data as Campaign;
}

/**
 * Copies the current rules into a new independent draft (EDIT level). The name defaults to
 * "<name> (copy)". Resolves to the new campaign; open `campaignEditPath(copy.id)` next.
 */
export async function cloneCampaign(
  id: string,
  input: { name?: string } = {},
): Promise<Campaign> {
  const { data } = await getApiClient().POST("/api/v1/campaigns/{id}/clone/", {
    params: { path: { id } },
    body: input,
  });
  return data as Campaign;
}

/** Draft to active (EDIT level). */
export async function activateCampaign(id: string): Promise<Campaign> {
  const { data } = await getApiClient().POST(
    "/api/v1/campaigns/{id}/activate/",
    { params: { path: { id } } },
  );
  return data as Campaign;
}

/** MANAGE level (admin). */
export async function archiveCampaign(id: string): Promise<Campaign> {
  const { data } = await getApiClient().POST(
    "/api/v1/campaigns/{id}/archive/",
    { params: { path: { id } } },
  );
  return data as Campaign;
}

/** MANAGE level (admin). */
export async function restoreCampaign(id: string): Promise<Campaign> {
  const { data } = await getApiClient().POST(
    "/api/v1/campaigns/{id}/restore/",
    { params: { path: { id } } },
  );
  return data as Campaign;
}

/** Hook: a page of campaigns; refetches when `query` changes. `enabled: false` skips it. */
export function useCampaigns(
  query: CampaignListQuery = {},
  options: { enabled?: boolean } = {},
): ApiQueryState<CampaignPage> {
  return useApiQuery(
    `campaigns:${JSON.stringify(query)}`,
    (signal) => listCampaigns(query, signal),
    options,
  );
}

/** Hook: a page of one client's campaigns; pass `undefined` as `clientId` to skip fetching. */
export function useClientCampaigns(
  clientId: string | undefined,
  query: Omit<CampaignListQuery, "client"> = {},
): ApiQueryState<CampaignPage> {
  return useApiQuery(
    `client-campaigns:${clientId ?? ""}:${JSON.stringify(query)}`,
    (signal) => listClientCampaigns(clientId as string, query, signal),
    { enabled: Boolean(clientId) },
  );
}

/** Hook: one campaign; pass `undefined` to skip fetching. */
export function useCampaign(id: string | undefined): ApiQueryState<Campaign> {
  return useApiQuery(
    `campaign:${id ?? ""}`,
    (signal) => getCampaign(id as string, signal),
    { enabled: Boolean(id) },
  );
}
