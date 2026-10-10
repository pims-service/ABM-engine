"use client";

import Link from "next/link";
import { useState } from "react";

import { ListSkeleton } from "@/components/ListSkeleton";
import { ListToolbar } from "@/components/ListToolbar";
import { StatusBadge } from "@/components/StatusBadge";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button, buttonStyles } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Pagination } from "@/components/ui/Pagination";
import { Select } from "@/components/ui/Select";
import { Table, Td, Th } from "@/components/ui/Table";
import { useAccess } from "@/features/access/AccessProvider";
import { useOptionalSelection } from "@/features/selection/SelectionProvider";
import { buildListQuery } from "@/lib/api/list-query";
import { errorMessage } from "@/lib/format";
import { useListFilters } from "@/lib/hooks/useListFilters";

import {
  campaignEditPath,
  campaignNewPath,
  type CampaignStatus,
  useCampaigns,
  useClientCampaigns,
} from "./api";
import { summarizeIcp } from "./icp";
import { useCampaignActions } from "./useCampaignActions";

export const CAMPAIGNS_PAGE_SIZE = 10;

const STATUS_OPTIONS: ReadonlyArray<{ value: CampaignStatus; label: string }> =
  [
    { value: "draft", label: "Draft" },
    { value: "active", label: "Active" },
    { value: "archived", label: "Archived" },
  ];

/**
 * Filterable, paginated campaign table with row actions. With `clientId` it lists that client's
 * campaigns (client detail page); without it, all campaigns you can see, with a client filter.
 */
export function CampaignList({ clientId }: { clientId?: string }) {
  const access = useAccess();
  const selection = useOptionalSelection();
  const filters = useListFilters<CampaignStatus>();
  const [clientFilter, setClientFilter] = useState("");

  const base = buildListQuery(filters.filters, {
    pageSize: CAMPAIGNS_PAGE_SIZE,
    ordering: "name",
  });
  const all = useCampaigns(
    { ...base, ...(clientFilter ? { client: clientFilter } : {}) },
    { enabled: !clientId },
  );
  const nested = useClientCampaigns(clientId, base);
  const { data, error, loading, reload } = clientId ? nested : all;
  const actions = useCampaignActions(reload);

  const clientNames = new Map(
    (selection?.clients ?? []).map((client) => [client.id, client.name]),
  );
  const newClientId =
    clientId ?? (clientFilter || selection?.clientId) ?? undefined;
  // Without a client in play, offer "New campaign" unless every listed client denied editing.
  const canCreate = newClientId
    ? access.can("edit", newClientId)
    : access.can("edit") &&
      (!data?.results.length ||
        data.results.some((campaign) => access.can("edit", campaign.client)));
  const newLink = (
    <Link
      href={campaignNewPath(newClientId)}
      className={buttonStyles({ variant: "primary" })}
    >
      New campaign
    </Link>
  );
  const rows = data?.results ?? [];
  const isFiltered = filters.isFiltered || clientFilter !== "";

  return (
    <>
      <ListToolbar
        filters={filters}
        searchLabel="Search campaigns"
        statuses={STATUS_OPTIONS}
      >
        {!clientId ? (
          <Select
            label="Client"
            value={clientFilter}
            onChange={(event) => setClientFilter(event.target.value)}
          >
            <option value="">All clients</option>
            {(selection?.clients ?? []).map((client) => (
              <option key={client.id} value={client.id}>
                {client.name}
              </option>
            ))}
          </Select>
        ) : null}
      </ListToolbar>
      {canCreate ? (
        <div className="mb-4 flex justify-end">{newLink}</div>
      ) : null}
      {actions.notice ? (
        <Alert tone={actions.notice.tone} className="mb-4">
          {actions.notice.text}
        </Alert>
      ) : null}

      {error && !data ? (
        <Alert
          tone="danger"
          action={
            <Button variant="secondary" size="sm" onClick={reload}>
              Try again
            </Button>
          }
        >
          Could not load campaigns. {errorMessage(error)}
        </Alert>
      ) : !data ? (
        <ListSkeleton label="Loading campaigns" />
      ) : rows.length === 0 ? (
        isFiltered ? (
          <EmptyState
            title="No campaigns match"
            description="Try a different search or clear the filters."
            action={
              <Button
                variant="secondary"
                onClick={() => {
                  filters.reset();
                  setClientFilter("");
                }}
              >
                Clear filters
              </Button>
            }
          />
        ) : (
          <EmptyState
            title="No campaigns yet"
            description={
              canCreate
                ? "A campaign holds the ICP rules for one outreach effort. Create the first one."
                : "There are no campaigns to show."
            }
            action={canCreate ? newLink : undefined}
          />
        )
      ) : (
        <div aria-busy={loading}>
          {error ? (
            <Alert tone="danger" className="mb-4">
              Could not refresh the list. {errorMessage(error)}
            </Alert>
          ) : null}
          <Table caption="Campaigns">
            <thead>
              <tr>
                <Th>Campaign</Th>
                {!clientId ? <Th>Client</Th> : null}
                <Th>Status</Th>
                <Th>ICP</Th>
                <Th>Profile</Th>
                <Th>
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </thead>
            <tbody className={loading ? "opacity-60" : undefined}>
              {rows.map((campaign) => {
                const icp = summarizeIcp(campaign);
                const busy = actions.busyId === campaign.id;
                const archived = campaign.status === "archived";
                const isCurrent = selection?.campaignId === campaign.id;
                return (
                  <tr key={campaign.id}>
                    <Td>
                      <span className="font-medium">{campaign.name}</span>
                      {isCurrent ? (
                        <Badge tone="info" className="ml-2">
                          Current
                        </Badge>
                      ) : null}
                    </Td>
                    {!clientId ? (
                      <Td className="text-fg-muted">
                        {clientNames.get(campaign.client) ?? "—"}
                      </Td>
                    ) : null}
                    <Td>
                      <StatusBadge status={campaign.status} />
                    </Td>
                    <Td>
                      <dl className="space-y-0.5 text-xs">
                        <IcpLine label="Countries" value={icp.countries} />
                        <IcpLine label="Industries" value={icp.industries} />
                        <IcpLine label="Size" value={icp.size} />
                      </dl>
                    </Td>
                    <Td className="whitespace-nowrap text-fg-muted">
                      {icp.version}
                    </Td>
                    <Td>
                      <div className="flex flex-wrap justify-end gap-1.5">
                        {!archived && !isCurrent && selection ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            aria-label={`Use ${campaign.name} as current campaign`}
                            onClick={() => selection.selectCampaign(campaign)}
                          >
                            Use
                          </Button>
                        ) : null}
                        {!archived && access.can("edit", campaign.client) ? (
                          <Link
                            href={campaignEditPath(campaign.id)}
                            aria-label={`Edit ${campaign.name}`}
                            className={buttonStyles({
                              variant: "secondary",
                              size: "sm",
                            })}
                          >
                            Edit
                          </Link>
                        ) : null}
                        {access.can("edit", campaign.client) ? (
                          <Button
                            variant="secondary"
                            size="sm"
                            disabled={busy}
                            aria-label={`Clone ${campaign.name}`}
                            onClick={() => void actions.clone(campaign)}
                          >
                            Clone
                          </Button>
                        ) : null}
                        {campaign.status === "draft" &&
                        access.can("edit", campaign.client) ? (
                          <Button
                            variant="secondary"
                            size="sm"
                            disabled={busy}
                            aria-label={`Activate ${campaign.name}`}
                            onClick={() => void actions.activate(campaign)}
                          >
                            Activate
                          </Button>
                        ) : null}
                        {access.can("manage", campaign.client) ? (
                          archived ? (
                            <Button
                              variant="secondary"
                              size="sm"
                              disabled={busy}
                              aria-label={`Restore ${campaign.name}`}
                              onClick={() => actions.restore(campaign)}
                            >
                              Restore
                            </Button>
                          ) : (
                            <Button
                              variant="secondary"
                              size="sm"
                              disabled={busy}
                              aria-label={`Archive ${campaign.name}`}
                              onClick={() => actions.archive(campaign)}
                            >
                              Archive
                            </Button>
                          )
                        ) : null}
                      </div>
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
          <Pagination
            page={filters.filters.page}
            pageSize={CAMPAIGNS_PAGE_SIZE}
            count={data.count}
            disabled={loading}
            onPageChange={filters.setPage}
          />
        </div>
      )}
      {actions.dialogs}
    </>
  );
}

function IcpLine({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="inline text-fg-muted">{label}: </dt>
      <dd className="inline">{value}</dd>
    </div>
  );
}
