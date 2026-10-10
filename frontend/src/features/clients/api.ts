/**
 * Clients API: typed fetch helpers and data hooks. Shared building blocks (the switcher, the lists
 * and the campaign form import from here), so keep the exports stable.
 *
 * All helpers reject with `ApiError` (see `@/lib/api`) and take an optional `AbortSignal`.
 */
import { type components, getApiClient } from "@/lib/api";
import type { ListQuery } from "@/lib/api/list-query";
import { type ApiQueryState, useApiQuery } from "@/lib/hooks/useApiQuery";

export type Client = components["schemas"]["Client"];
export type ClientStatus = components["schemas"]["ClientStatusEnum"];
/** Body of create and (partial) edit: `name` is required on create. */
export type ClientInput = components["schemas"]["ClientRequest"];
export type ClientPage = components["schemas"]["PaginatedClientList"];
export type ClientListQuery = ListQuery<ClientStatus>;

export const CLIENT_STATUSES: readonly ClientStatus[] = ["active", "archived"];

/** One page of clients. Archived ones are hidden unless `query.archived`/`status` ask for them. */
export async function listClients(
  query: ClientListQuery = {},
  signal?: AbortSignal,
): Promise<ClientPage> {
  const { data } = await getApiClient().GET("/api/v1/clients/", {
    params: { query },
    signal,
  });
  return data as ClientPage;
}

export async function getClient(
  id: string,
  signal?: AbortSignal,
): Promise<Client> {
  const { data } = await getApiClient().GET("/api/v1/clients/{id}/", {
    params: { path: { id } },
    signal,
  });
  return data as Client;
}

/** Needs the MANAGE level (admin); the creator becomes the client's admin. */
export async function createClient(input: ClientInput): Promise<Client> {
  const { data } = await getApiClient().POST("/api/v1/clients/", {
    body: input,
  });
  return data as Client;
}

/** Edit name and/or notes (EDIT level: manager and admin). */
export async function updateClient(
  id: string,
  input: Partial<ClientInput>,
): Promise<Client> {
  const { data } = await getApiClient().PATCH("/api/v1/clients/{id}/", {
    params: { path: { id } },
    body: input,
  });
  return data as Client;
}

/** MANAGE level. Fails with 409 `client_has_active_jobs` while jobs are queued or running. */
export async function archiveClient(id: string): Promise<Client> {
  const { data } = await getApiClient().POST("/api/v1/clients/{id}/archive/", {
    params: { path: { id } },
  });
  return data as Client;
}

/** MANAGE level. Fails with a `name` field error if an active client uses the name now. */
export async function restoreClient(id: string): Promise<Client> {
  const { data } = await getApiClient().POST("/api/v1/clients/{id}/restore/", {
    params: { path: { id } },
  });
  return data as Client;
}

/** Hook: a page of clients; refetches when `query` changes. */
export function useClients(
  query: ClientListQuery = {},
): ApiQueryState<ClientPage> {
  return useApiQuery(`clients:${JSON.stringify(query)}`, (signal) =>
    listClients(query, signal),
  );
}

/** Hook: one client; pass `undefined` to skip fetching. */
export function useClient(id: string | undefined): ApiQueryState<Client> {
  return useApiQuery(
    `client:${id ?? ""}`,
    (signal) => getClient(id as string, signal),
    { enabled: Boolean(id) },
  );
}
