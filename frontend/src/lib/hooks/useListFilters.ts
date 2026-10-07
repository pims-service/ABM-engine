"use client";

import { useCallback, useState } from "react";

import type { ListFilters } from "@/lib/api/list-query";

import { useDebouncedValue } from "./useDebouncedValue";

export interface UseListFilters<Status extends string> {
  /** What the search box shows right now (not debounced). */
  searchInput: string;
  /** Filters to turn into a query: the search term is debounced, `page` resets on any change. */
  filters: ListFilters<Status>;
  setSearch: (value: string) => void;
  /** Choosing `archived` also switches "Show archived" on. */
  setStatus: (value: Status | "") => void;
  /** Switching it off also clears an `archived` status filter. */
  setShowArchived: (value: boolean) => void;
  setPage: (page: number) => void;
  reset: () => void;
  /** True when any filter narrows the list (search, status or archived shown). */
  isFiltered: boolean;
}

/**
 * State for a list screen's toolbar: debounced search, status filter, "Show archived" toggle
 * (default off) and the page. Changing a filter returns to page 1 without an extra request.
 */
export function useListFilters<
  Status extends string,
>(): UseListFilters<Status> {
  const [searchInput, setSearch] = useState("");
  const [status, setStatusState] = useState<Status | "">("");
  const [showArchived, setShowArchivedState] = useState(false);
  const [pageState, setPageState] = useState({ page: 1, key: "" });
  const search = useDebouncedValue(searchInput, 300);

  const filterKey = `${search.trim()}|${status}|${showArchived}`;
  const page = pageState.key === filterKey ? pageState.page : 1;

  const setStatus = useCallback((value: Status | "") => {
    setStatusState(value);
    if (value === "archived") setShowArchivedState(true);
  }, []);

  const setShowArchived = useCallback((value: boolean) => {
    setShowArchivedState(value);
    if (!value) setStatusState((s) => (s === "archived" ? "" : s));
  }, []);

  const setPage = useCallback(
    (next: number) => setPageState({ page: next, key: filterKey }),
    [filterKey],
  );

  const reset = useCallback(() => {
    setSearch("");
    setStatusState("");
    setShowArchivedState(false);
  }, []);

  return {
    searchInput,
    filters: { search, status, showArchived, page },
    setSearch,
    setStatus,
    setShowArchived,
    setPage,
    reset,
    isFiltered: searchInput.trim() !== "" || status !== "" || showArchived,
  };
}
