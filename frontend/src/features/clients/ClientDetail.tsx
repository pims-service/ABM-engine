"use client";

import Link from "next/link";

import { StatusBadge } from "@/components/StatusBadge";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { useAccess } from "@/features/access/AccessProvider";
import { CampaignList } from "@/features/campaigns/CampaignList";
import { useOptionalSelection } from "@/features/selection/SelectionProvider";
import { ApiError } from "@/lib/api";
import { formatDate } from "@/lib/format";

import { useClient } from "./api";
import { useClientActions } from "./useClientActions";

/** `/clients/[id]`: the client's details and actions, with its campaigns underneath. */
export function ClientDetail({ id }: { id: string }) {
  const access = useAccess();
  const selection = useOptionalSelection();
  const { data: client, error, loading, reload } = useClient(id);
  const actions = useClientActions(() => reload());

  const back = (
    <Link
      href="/clients"
      className="mb-4 inline-block text-sm text-accent underline-offset-2 hover:underline"
    >
      &larr; All clients
    </Link>
  );

  if (!client) {
    if (error) {
      const missing =
        error instanceof ApiError &&
        (error.status === 404 || error.status === 403);
      return (
        <>
          {back}
          {missing ? (
            <EmptyState
              title="Client not found"
              description="It may have been removed, or you are not a member of it."
            />
          ) : (
            <Alert
              tone="danger"
              action={
                <Button variant="secondary" size="sm" onClick={reload}>
                  Try again
                </Button>
              }
            >
              Could not load this client. {error.message}
            </Alert>
          )}
        </>
      );
    }
    return (
      <>
        {back}
        <div role="status" aria-live="polite" className="flex flex-col gap-3">
          <span className="sr-only">Loading client…</span>
          <Skeleton className="h-8 w-1/3" />
          <Skeleton className="h-4 w-2/3" />
        </div>
      </>
    );
  }

  const archived = client.status === "archived";
  const isCurrent = selection?.clientId === client.id;

  return (
    <>
      {back}
      <PageHeader
        title={client.name}
        description={client.notes || "No notes yet."}
        actions={
          <div className="flex flex-wrap justify-end gap-2" aria-busy={loading}>
            {!archived && selection && !isCurrent ? (
              <Button
                variant="secondary"
                onClick={() => selection.selectClient(client.id)}
              >
                Set as current client
              </Button>
            ) : null}
            {!archived && access.can("edit", client.id) ? (
              <Button variant="secondary" onClick={() => actions.edit(client)}>
                Edit
              </Button>
            ) : null}
            {access.can("manage", client.id) ? (
              archived ? (
                <Button
                  variant="secondary"
                  onClick={() => actions.restore(client)}
                >
                  Restore
                </Button>
              ) : (
                <Button
                  variant="secondary"
                  onClick={() => actions.archive(client)}
                >
                  Archive
                </Button>
              )
            ) : null}
          </div>
        }
      />
      <div className="-mt-3 mb-6 flex flex-wrap items-center gap-2 text-sm text-fg-muted">
        <StatusBadge status={client.status} />
        {isCurrent ? <Badge tone="info">Current client</Badge> : null}
        <span>Created {formatDate(client.created_at)}</span>
        <span aria-hidden="true">&middot;</span>
        <span>Updated {formatDate(client.updated_at)}</span>
      </div>
      {actions.notice ? (
        <Alert tone="success" className="mb-4">
          {actions.notice}
        </Alert>
      ) : null}

      <section aria-labelledby="client-campaigns-heading">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 id="client-campaigns-heading" className="text-lg font-semibold">
            Campaigns
          </h2>
        </div>
        <CampaignList clientId={client.id} />
      </section>
      {actions.dialogs}
    </>
  );
}
