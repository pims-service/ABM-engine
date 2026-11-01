/**
 * Finding companies at a data provider and importing the ones you tick (Brief section 4C).
 * All helpers reject with `ApiError`.
 */
import { type components, getApiClient } from "@/lib/api";

export type Provider = components["schemas"]["ProviderCapabilities"];
export type ProviderSearch = components["schemas"]["ProviderSearchResponse"];
export type PreviewItem = components["schemas"]["PreviewItem"];
export type ProviderImportBatch = components["schemas"]["ProviderImportBatch"];
export type SearchFilters = components["schemas"]["SearchFiltersRequest"];
type ProviderCompanyRequest = components["schemas"]["ProviderCompanyRequest"];

/** Companies per page of results (the server's own default). */
const PAGE_SIZE = 25;

/** The providers this deployment can search (the offline test provider is listed in dev). */
export async function listProviders(signal?: AbortSignal): Promise<Provider[]> {
  const { data } = await getApiClient().GET("/api/v1/providers/", { signal });
  const page = data as { results?: Provider[] } | Provider[] | undefined;
  if (Array.isArray(page)) return page;
  return page?.results ?? [];
}

export interface SearchInput {
  provider: string;
  query?: string;
  filters?: SearchFilters;
  cursor?: string | null;
}

/**
 * Search with the campaign's own filters, replaced by any key in `filters` (an empty list or
 * `null` clears that filter). Every result says whether the company already exists.
 */
export async function searchProvider(
  campaignId: string,
  input: SearchInput,
): Promise<ProviderSearch> {
  const { data } = await getApiClient().POST(
    "/api/v1/campaigns/{id}/provider-search/",
    {
      params: { path: { id: campaignId } },
      body: {
        provider: input.provider,
        query: input.query ?? "",
        filters: input.filters,
        page_size: PAGE_SIZE,
        cursor: input.cursor ?? undefined,
      },
    },
  );
  return data as ProviderSearch;
}

/** Import the companies exactly as the search returned them. */
export async function importSelected(
  campaignId: string,
  input: {
    provider: string;
    companies: PreviewItem["company"][];
    search: { query?: string; filters?: SearchFilters };
    retrievedAt: string;
  },
): Promise<ProviderImportBatch> {
  const { data } = await getApiClient().POST(
    "/api/v1/campaigns/{id}/provider-import/",
    {
      params: { path: { id: campaignId } },
      body: {
        provider: input.provider,
        companies: input.companies as ProviderCompanyRequest[],
        search: { ...input.search, page_size: PAGE_SIZE },
        retrieved_at: input.retrievedAt,
      },
    },
  );
  return data as ProviderImportBatch;
}

export async function getProviderImport(
  batchId: string,
  signal?: AbortSignal,
): Promise<ProviderImportBatch> {
  const { data } = await getApiClient().GET("/api/v1/provider-imports/{id}/", {
    params: { path: { id: batchId } },
    signal,
  });
  return data as ProviderImportBatch;
}
