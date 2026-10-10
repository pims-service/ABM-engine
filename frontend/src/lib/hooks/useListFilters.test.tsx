import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useListFilters } from "./useListFilters";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("useListFilters", () => {
  it("starts with archived hidden and nothing filtered", () => {
    const { result } = renderHook(() =>
      useListFilters<"active" | "archived">(),
    );
    expect(result.current.filters).toEqual({
      search: "",
      status: "",
      showArchived: false,
      page: 1,
    });
    expect(result.current.isFiltered).toBe(false);
  });

  it("debounces the search but shows typing at once", () => {
    const { result } = renderHook(() => useListFilters<"active">());
    act(() => result.current.setSearch("ac"));
    expect(result.current.searchInput).toBe("ac");
    expect(result.current.filters.search).toBe("");
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(result.current.filters.search).toBe("ac");
    expect(result.current.isFiltered).toBe(true);
  });

  it("returns to page 1 when a filter changes", () => {
    const { result } = renderHook(() => useListFilters<"active">());
    act(() => result.current.setPage(3));
    expect(result.current.filters.page).toBe(3);
    act(() => result.current.setStatus("active"));
    expect(result.current.filters.page).toBe(1);
  });

  it("keeps the status and the archived toggle consistent", () => {
    const { result } = renderHook(() =>
      useListFilters<"active" | "archived">(),
    );
    act(() => result.current.setStatus("archived"));
    expect(result.current.filters.showArchived).toBe(true);
    act(() => result.current.setShowArchived(false));
    expect(result.current.filters.status).toBe("");
  });

  it("reset clears everything", () => {
    const { result } = renderHook(() => useListFilters<"active">());
    act(() => {
      result.current.setStatus("active");
      result.current.setShowArchived(true);
    });
    act(() => result.current.reset());
    expect(result.current.isFiltered).toBe(false);
  });
});
