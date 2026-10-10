/** Helpers shared by the paginated, filterable list endpoints (clients, campaigns). */

export type ArchivedFilter = "false" | "true" | "all";

/** Filters as the list screens hold them. */
export interface ListFilters<Status extends string> {
  search: string;
  status: Status | "";
  /** The "Show archived" toggle. */
  showArchived: boolean;
  page: number;
}

/** Query string values the list endpoints understand. */
export interface ListQuery<Status extends string> {
  search?: string;
  status?: Status;
  archived?: ArchivedFilter;
  ordering?: string;
  page?: number;
  page_size?: number;
}

/**
 * Maps the screen filters to API query parameters. Archived items are hidden unless the toggle is
 * on (`archived=all`) or the status filter asks for `archived` (which implies showing them).
 */
export function buildListQuery<Status extends string>(
  filters: ListFilters<Status>,
  options: { pageSize?: number; ordering?: string } = {},
): ListQuery<Status> {
  const search = filters.search.trim();
  const showArchived = filters.showArchived || filters.status === "archived";
  return {
    ...(search ? { search } : {}),
    ...(filters.status ? { status: filters.status } : {}),
    archived: showArchived ? "all" : "false",
    ...(options.ordering ? { ordering: options.ordering } : {}),
    ...(filters.page > 1 ? { page: filters.page } : {}),
    ...(options.pageSize ? { page_size: options.pageSize } : {}),
  };
}
