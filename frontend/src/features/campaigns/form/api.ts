import type { Client } from "@/features/clients/api";
import { getApiClient } from "@/lib/api";

import type {
  Campaign,
  CampaignPatchRequest,
  CampaignProfile,
  CampaignWriteRequest,
} from "./values";

export type { Client };

/** Clients the user can pick (active ones; the API paginates by up to 100). */
export async function fetchClients(signal?: AbortSignal): Promise<Client[]> {
  const clients: Client[] = [];
  for (let page = 1; page <= 20; page += 1) {
    const { data } = await getApiClient().GET("/api/v1/clients/", {
      params: { query: { page, page_size: 100, status: "active" } },
      signal,
    });
    clients.push(...(data?.results ?? []));
    if (!data?.next) break;
  }
  return clients;
}

export async function fetchClient(
  id: string,
  signal?: AbortSignal,
): Promise<Client | null> {
  const { data } = await getApiClient().GET("/api/v1/clients/{id}/", {
    params: { path: { id } },
    signal,
  });
  return data ?? null;
}

export async function fetchCampaign(
  id: string,
  signal?: AbortSignal,
): Promise<Campaign> {
  const { data } = await getApiClient().GET("/api/v1/campaigns/{id}/", {
    params: { path: { id } },
    signal,
  });
  if (!data) throw new Error("Empty response.");
  return data;
}

export async function fetchProfileVersions(
  id: string,
  signal?: AbortSignal,
): Promise<CampaignProfile[]> {
  const { data } = await getApiClient().GET(
    "/api/v1/campaigns/{id}/profile-versions/",
    { params: { path: { id }, query: { page_size: 100 } }, signal },
  );
  return data?.results ?? [];
}

export async function createCampaign(
  body: CampaignWriteRequest,
): Promise<Campaign> {
  const { data } = await getApiClient().POST("/api/v1/campaigns/", { body });
  if (!data) throw new Error("Empty response.");
  return data;
}

export interface UpdateResult {
  campaign: Campaign;
  /**
   * `X-Profile-Version-Created`: true when the request made a new profile version, false when the
   * rules equalled the current one, null when the header was not readable (for example a
   * cross-origin API that does not expose it).
   */
  versionCreated: boolean | null;
}

export async function updateCampaign(
  id: string,
  body: CampaignPatchRequest,
): Promise<UpdateResult> {
  const { data, response } = await getApiClient().PATCH(
    "/api/v1/campaigns/{id}/",
    { params: { path: { id } }, body },
  );
  if (!data) throw new Error("Empty response.");
  const header = response.headers.get("X-Profile-Version-Created");
  return {
    campaign: data,
    versionCreated: header === null ? null : header.toLowerCase() === "true",
  };
}
