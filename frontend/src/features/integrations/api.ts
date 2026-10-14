/**
 * Integration connections API (issue #77): typed helpers over `/api/v1/integrations/connections/`
 * and a data hook. All helpers reject with `ApiError`.
 *
 * Credentials are write-only: they go out in `createConnection` / `updateConnection` and never
 * come back (responses carry `has_credentials` and a masked `credentials_hint`). Never log these
 * bodies and never put a secret in a URL.
 */
import { type components, getApiClient } from "@/lib/api";
import { type ApiQueryState, useApiQuery } from "@/lib/hooks/useApiQuery";

export type Connection = components["schemas"]["Connection"];
export type ConnectionPage = components["schemas"]["PaginatedConnectionList"];
export type ConnectionCreateInput =
  components["schemas"]["ConnectionCreateRequest"];
export type ConnectionUpdateInput =
  components["schemas"]["PatchedConnectionUpdateRequest"];
export type ConnectionTestResult =
  components["schemas"]["ConnectionTestResult"];
export type ConnectionCategory = components["schemas"]["CategoryEnum"];
export type ConnectionStatus = components["schemas"]["ConnectionStatusEnum"];

export interface ConnectionListQuery {
  client: string;
  archived?: "false" | "true" | "all";
  category?: ConnectionCategory;
  page_size?: number;
  ordering?: string;
}

/** Needs the EDIT level (manager, admin): others get 403. */
export async function listConnections(
  query: ConnectionListQuery,
  signal?: AbortSignal,
): Promise<ConnectionPage> {
  const { data } = await getApiClient().GET(
    "/api/v1/integrations/connections/",
    {
      params: { query },
      signal,
    },
  );
  return data as ConnectionPage;
}

/** MANAGE level (admin). `credentials` is write-only. */
export async function createConnection(
  input: ConnectionCreateInput,
): Promise<Connection> {
  const { data } = await getApiClient().POST(
    "/api/v1/integrations/connections/",
    {
      body: input,
    },
  );
  return data as Connection;
}

/**
 * MANAGE level. `name` and `config` change those fields; `credentials` replaces all stored
 * credentials, `null` removes them and leaving it out keeps them.
 */
export async function updateConnection(
  id: string,
  input: ConnectionUpdateInput,
): Promise<Connection> {
  const { data } = await getApiClient().PATCH(
    "/api/v1/integrations/connections/{id}/",
    { params: { path: { id } }, body: input },
  );
  return data as Connection;
}

export type ConnectionTransition = "enable" | "disable" | "archive" | "restore";

const TRANSITION_PATHS = {
  enable: "/api/v1/integrations/connections/{id}/enable/",
  disable: "/api/v1/integrations/connections/{id}/disable/",
  archive: "/api/v1/integrations/connections/{id}/archive/",
  restore: "/api/v1/integrations/connections/{id}/restore/",
} as const;

/** Enable, disable, archive or restore (MANAGE level). */
export async function transitionConnection(
  id: string,
  action: ConnectionTransition,
): Promise<Connection> {
  const { data } = await getApiClient().POST(TRANSITION_PATHS[action], {
    params: { path: { id } },
  });
  return data as Connection;
}

/**
 * Run the provider's check with the stored credentials (MANAGE level, 5 per minute per
 * connection: 429 `throttled`). `message` is untrusted text: render it as text only.
 */
export async function testConnection(
  id: string,
): Promise<ConnectionTestResult> {
  const { data } = await getApiClient().POST(
    "/api/v1/integrations/connections/{id}/test/",
    { params: { path: { id } } },
  );
  return data as ConnectionTestResult;
}

/** Hook: a client's connections (first 100). Pass `undefined` to skip (no client selected). */
export function useConnections(
  clientId: string | undefined,
  options: { archived: "false" | "all" },
): ApiQueryState<ConnectionPage> {
  return useApiQuery(
    `connections:${clientId ?? ""}:${options.archived}`,
    (signal) =>
      listConnections(
        {
          client: clientId as string,
          archived: options.archived,
          page_size: 100,
          ordering: "category,name",
        },
        signal,
      ),
    { enabled: Boolean(clientId) },
  );
}
