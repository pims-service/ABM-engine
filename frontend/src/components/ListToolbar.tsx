"use client";

import type { ReactNode } from "react";

import { Checkbox } from "@/components/ui/Checkbox";
import { Select } from "@/components/ui/Select";
import { TextField } from "@/components/ui/TextField";
import type { UseListFilters } from "@/lib/hooks/useListFilters";

/** Search box, status filter and "Show archived" toggle for a list screen. */
export function ListToolbar<Status extends string>({
  filters,
  searchLabel,
  statuses,
  children,
}: {
  filters: UseListFilters<Status>;
  searchLabel: string;
  statuses: ReadonlyArray<{ value: Status; label: string }>;
  /** Extra filters (for example a client picker), rendered before the status filter. */
  children?: ReactNode;
}) {
  return (
    <div
      role="search"
      aria-label={searchLabel}
      className="mb-4 flex flex-wrap items-end gap-x-4 gap-y-3"
    >
      <div className="min-w-48 flex-1 sm:max-w-xs">
        <TextField
          label={searchLabel}
          type="search"
          placeholder="Search by name"
          value={filters.searchInput}
          onChange={(event) => filters.setSearch(event.target.value)}
        />
      </div>
      {children}
      <Select
        label="Status"
        value={filters.filters.status}
        onChange={(event) =>
          filters.setStatus(event.target.value as Status | "")
        }
      >
        <option value="">All statuses</option>
        {statuses.map((status) => (
          <option key={status.value} value={status.value}>
            {status.label}
          </option>
        ))}
      </Select>
      <Checkbox
        label="Show archived"
        className="pb-2.5"
        checked={filters.filters.showArchived}
        onChange={(event) => filters.setShowArchived(event.target.checked)}
      />
    </div>
  );
}
