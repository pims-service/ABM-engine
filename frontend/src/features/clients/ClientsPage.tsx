"use client";

import Link from "next/link";

import { ListSkeleton } from "@/components/ListSkeleton";
import { ListToolbar } from "@/components/ListToolbar";
import { StatusBadge } from "@/components/StatusBadge";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Pagination } from "@/components/ui/Pagination";
import { Table, Td, Th } from "@/components/ui/Table";
import { useAccess } from "@/features/access/AccessProvider";
import { buildListQuery } from "@/lib/api/list-query";
import { errorMessage, formatDate } from "@/lib/format";
import { useListFilters } from "@/lib/hooks/useListFilters";

import { type ClientStatus, useClients } from "./api";
import { useClientActions } from "./useClientActions";

export const CLIENTS_PAGE_SIZE = 10;

const STATUS_OPTIONS: ReadonlyArray<{ value: ClientStatus; label: string }> = [
  { value: "active", label: "Active" },
  { value: "archived", label: "Archived" },
];

/** `/clients`: search, status filter, "Show archived", pagination and row actions. */
export function ClientsPage() {
  const access = useAccess();
  const filters = useListFilters<ClientStatus>();
  const query = buildListQuery(filters.filters, {
    pageSize: CLIENTS_PAGE_SIZE,
    ordering: "name",
  });
  const { data, error, loading, reload } = useClients(query);
  const actions = useClientActions(() => reload());

  const canCreate = access.can("manage");
  const count = data?.count ?? 0;
  const rows = data?.results ?? [];

  return (
    <>
      <PageHeader
        title="Clients"
        description="The companies you run campaigns for."
        actions={
          canCreate ? (
            <Button onClick={actions.create}>New client</Button>
          ) : null
        }
      />
      {actions.notice ? (
        <Alert tone="success" className="mb-4">
          {actions.notice}
        </Alert>
      ) : null}
      <ListToolbar
        filters={filters}
        searchLabel="Search clients"
        statuses={STATUS_OPTIONS}
      />

      {error && !data ? (
        <Alert
          tone="danger"
          action={
            <Button variant="secondary" size="sm" onClick={reload}>
              Try again
            </Button>
          }
        >
          Could not load clients. {errorMessage(error)}
        </Alert>
      ) : !data ? (
        <ListSkeleton label="Loading clients" />
      ) : rows.length === 0 ? (
        filters.isFiltered ? (
          <EmptyState
            title="No clients match"
            description="Try a different search or clear the filters."
            action={
              <Button variant="secondary" onClick={filters.reset}>
                Clear filters
              </Button>
            }
          />
        ) : (
          <EmptyState
            title="No clients yet"
            description={
              canCreate
                ? "A client is the company you run campaigns for. Create your first one to get started."
                : "You are not a member of any client yet. Ask an admin to add you."
            }
            action={
              canCreate ? (
                <Button onClick={actions.create}>
                  Create your first client
                </Button>
              ) : undefined
            }
          />
        )
      ) : (
        <div aria-busy={loading}>
          {error ? (
            <Alert tone="danger" className="mb-4">
              Could not refresh the list. {errorMessage(error)}
            </Alert>
          ) : null}
          <Table caption="Clients">
            <thead>
              <tr>
                <Th>Name</Th>
                <Th>Status</Th>
                <Th className="hidden sm:table-cell">Updated</Th>
                <Th>
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </thead>
            <tbody className={loading ? "opacity-60" : undefined}>
              {rows.map((client) => (
                <tr key={client.id}>
                  <Td>
                    <Link
                      href={`/clients/${client.id}`}
                      className="font-medium text-accent underline-offset-2 hover:underline"
                    >
                      {client.name}
                    </Link>
                    {client.notes ? (
                      <p className="mt-0.5 line-clamp-1 max-w-md text-xs text-fg-muted">
                        {client.notes}
                      </p>
                    ) : null}
                  </Td>
                  <Td>
                    <StatusBadge status={client.status} />
                  </Td>
                  <Td className="hidden whitespace-nowrap text-fg-muted sm:table-cell">
                    {formatDate(client.updated_at)}
                  </Td>
                  <Td>
                    <div className="flex flex-wrap justify-end gap-1.5">
                      {client.status === "active" &&
                      access.can("edit", client.id) ? (
                        <Button
                          variant="secondary"
                          size="sm"
                          aria-label={`Edit ${client.name}`}
                          onClick={() => actions.edit(client)}
                        >
                          Edit
                        </Button>
                      ) : null}
                      {access.can("manage", client.id) ? (
                        client.status === "active" ? (
                          <Button
                            variant="secondary"
                            size="sm"
                            aria-label={`Archive ${client.name}`}
                            onClick={() => actions.archive(client)}
                          >
                            Archive
                          </Button>
                        ) : (
                          <Button
                            variant="secondary"
                            size="sm"
                            aria-label={`Restore ${client.name}`}
                            onClick={() => actions.restore(client)}
                          >
                            Restore
                          </Button>
                        )
                      ) : null}
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
          <Pagination
            page={filters.filters.page}
            pageSize={CLIENTS_PAGE_SIZE}
            count={count}
            disabled={loading}
            onPageChange={filters.setPage}
          />
        </div>
      )}
      {actions.dialogs}
    </>
  );
}
