"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ListSkeleton } from "@/components/ListSkeleton";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button, buttonStyles } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Table, Td, Th } from "@/components/ui/Table";
import { useAccess } from "@/features/access/AccessProvider";
import { useSelection } from "@/features/selection/SelectionProvider";
import { ApiError } from "@/lib/api";
import { errorMessage } from "@/lib/format";

import { type Connection, useConnections } from "./api";
import { CATEGORIES } from "./categories";
import { ConnectionStatusBadge, LastTest } from "./ConnectionBadges";
import { HintList } from "./CredentialsDialog";
import { ProviderSetup } from "./ProviderSetup";
import { type TestState, useConnectionActions } from "./useConnectionActions";

/** `/settings/integrations`: the selected client's provider connections. */
export function IntegrationsPage() {
  const { clientId, client, loading } = useSelection();
  if (!clientId) {
    return (
      <>
        <PageHeader
          title="Integrations"
          description="Provider connections for one client."
        />
        {loading ? (
          <ListSkeleton label="Loading clients" />
        ) : (
          <EmptyState
            title="Choose a client first"
            description="Integrations belong to a client. Pick one in the Current client selector in the header."
          />
        )}
      </>
    );
  }
  return (
    <ClientIntegrations
      key={clientId}
      clientId={clientId}
      clientName={client?.name ?? "this client"}
    />
  );
}

/** The connections of one client, grouped by category. */
export function ClientIntegrations({
  clientId,
  clientName,
}: {
  clientId: string;
  clientName: string;
}) {
  const access = useAccess();
  const [showArchived, setShowArchived] = useState(false);
  const { data, error, loading, reload } = useConnections(clientId, {
    archived: showArchived ? "all" : "false",
  });
  const actions = useConnectionActions(clientId, reload);

  const forbidden = error instanceof ApiError && error.status === 403;
  const { noteError } = access;
  useEffect(() => {
    if (error) noteError(error, "edit", clientId);
  }, [error, noteError, clientId]);

  if (forbidden || !access.can("edit", clientId)) {
    return (
      <>
        <PageHeader
          title="Integrations"
          description={`Provider connections for ${clientName}.`}
        />
        <EmptyState
          title="No access to integrations"
          description={`Integrations hold provider credentials, so only managers and admins of ${clientName} can open this page.`}
        />
      </>
    );
  }

  const canManage = access.can("manage", clientId);
  const connections = data?.results ?? [];

  return (
    <>
      <PageHeader
        title="Integrations"
        description={`Provider connections for ${clientName}. Keys are never shown again after they are saved.`}
        actions={
          <>
            <Link
              href="/settings/usage"
              className={buttonStyles({ variant: "secondary" })}
            >
              AI usage and limits
            </Link>
            {canManage ? (
              <Button onClick={() => actions.create()}>Add connection</Button>
            ) : null}
          </>
        }
      />
      {!canManage ? (
        <Alert tone="info" className="mb-4">
          You can view connections. Only admins can add, change or test them.
        </Alert>
      ) : null}
      {actions.notice ? (
        <Alert tone="success" className="mb-4">
          {actions.notice}
        </Alert>
      ) : null}
      <div className="mb-4">
        <Checkbox
          label="Show archived"
          checked={showArchived}
          onChange={(event) => setShowArchived(event.target.checked)}
        />
      </div>

      {error && !data ? (
        <Alert
          tone="danger"
          action={
            <Button variant="secondary" size="sm" onClick={reload}>
              Try again
            </Button>
          }
        >
          Could not load integrations. {errorMessage(error)}
        </Alert>
      ) : !data ? (
        <ListSkeleton label="Loading integrations" />
      ) : (
        <div className="flex flex-col gap-6" aria-busy={loading}>
          {error ? (
            <Alert tone="danger">
              Could not refresh the list. {errorMessage(error)}
            </Alert>
          ) : null}
          {data.next ? (
            <Alert tone="info">
              Showing the first {connections.length} connections.
            </Alert>
          ) : null}
          {canManage ? (
            <ProviderSetup
              clientId={clientId}
              connections={connections}
              onChanged={reload}
            />
          ) : null}
          {CATEGORIES.map((category) => {
            const items = connections.filter(
              (connection) => connection.category === category.value,
            );
            return (
              <section
                key={category.value}
                aria-labelledby={`cat-${category.value}`}
                className={loading ? "opacity-70" : undefined}
              >
                <div className="mb-2 flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h2
                      id={`cat-${category.value}`}
                      className="text-lg font-semibold"
                    >
                      {category.label}{" "}
                      <span className="text-sm font-normal text-fg-muted">
                        ({items.length})
                      </span>
                    </h2>
                    <p className="text-sm text-fg-muted">
                      {category.description}
                    </p>
                  </div>
                  {canManage ? (
                    <Button
                      variant="secondary"
                      size="sm"
                      aria-label={`Add ${category.label} connection`}
                      onClick={() => actions.create(category.value)}
                    >
                      Add
                    </Button>
                  ) : null}
                </div>
                {items.length === 0 ? (
                  <p className="rounded-lg border border-dashed border-line-strong px-4 py-3 text-sm text-fg-muted">
                    No connections yet.
                  </p>
                ) : (
                  <ConnectionTable
                    label={category.label}
                    items={items}
                    canManage={canManage}
                    actions={actions}
                  />
                )}
              </section>
            );
          })}
        </div>
      )}
      {actions.dialogs}
    </>
  );
}

function ConnectionTable({
  label,
  items,
  canManage,
  actions,
}: {
  label: string;
  items: Connection[];
  canManage: boolean;
  actions: ReturnType<typeof useConnectionActions>;
}) {
  return (
    <Table caption={`${label} connections`}>
      <thead>
        <tr>
          <Th>Connection</Th>
          <Th>Status</Th>
          <Th>Last test</Th>
          <Th>Credentials</Th>
          {canManage ? (
            <Th>
              <span className="sr-only">Actions</span>
            </Th>
          ) : null}
        </tr>
      </thead>
      <tbody>
        {items.map((connection) => (
          <ConnectionRow
            key={connection.id}
            connection={connection}
            canManage={canManage}
            actions={actions}
          />
        ))}
      </tbody>
    </Table>
  );
}

function TestLine({ state }: { state: TestState }) {
  if (state.state === "running") {
    return (
      <p role="status" className="mt-1 text-xs text-fg-muted">
        Testing…
      </p>
    );
  }
  if (state.state === "error") {
    return (
      <p role="alert" className="mt-1 text-xs font-medium text-danger-fg">
        {state.message}
      </p>
    );
  }
  const { result, message } = state.result;
  // `message` is untrusted provider text: a plain text node, never HTML.
  return (
    <p
      role="status"
      className={
        result === "ok"
          ? "mt-1 text-xs font-medium text-success-fg"
          : result === "failed"
            ? "mt-1 text-xs font-medium text-danger-fg"
            : "mt-1 text-xs font-medium text-info-fg"
      }
    >
      {result === "ok"
        ? "Test passed. "
        : result === "failed"
          ? "Test failed. "
          : "Not tested. "}
      {message}
    </p>
  );
}

function ConnectionRow({
  connection,
  canManage,
  actions,
}: {
  connection: Connection;
  canManage: boolean;
  actions: ReturnType<typeof useConnectionActions>;
}) {
  const archived = connection.archived_at !== null;
  const testState = actions.tests[connection.id];
  const shown =
    testState?.state === "done" ? testState.result.connection : connection;
  const name = connection.name;

  return (
    <tr>
      <Td>
        <p className="font-medium">{name}</p>
        <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-fg-muted">
          <span className="font-mono">{connection.provider}</span>
          {archived ? (
            <Badge tone="neutral" icon="▣">
              Archived
            </Badge>
          ) : null}
        </p>
      </Td>
      <Td>
        <ConnectionStatusBadge status={shown.status} />
      </Td>
      <Td>
        <LastTest connection={shown} />
        {testState ? <TestLine state={testState} /> : null}
      </Td>
      <Td>
        {connection.has_credentials ? (
          <>
            <span>Stored</span>
            <HintList connection={connection} />
          </>
        ) : (
          <span className="text-fg-muted">Not set</span>
        )}
      </Td>
      {canManage ? (
        <Td>
          <div className="flex flex-wrap justify-end gap-1.5">
            {archived ? (
              <Button
                variant="secondary"
                size="sm"
                aria-label={`Restore ${name}`}
                onClick={() => actions.transition("restore", connection)}
              >
                Restore
              </Button>
            ) : (
              <>
                <Button
                  variant="secondary"
                  size="sm"
                  aria-label={`Test connection ${name}`}
                  disabled={testState?.state === "running"}
                  onClick={() => actions.test(connection)}
                >
                  {testState?.state === "running" ? "Testing…" : "Test"}
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  aria-label={`Edit ${name}`}
                  onClick={() => actions.edit(connection)}
                >
                  Edit
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  aria-label={`Credentials for ${name}`}
                  onClick={() => actions.credentials(connection)}
                >
                  Credentials
                </Button>
                {connection.status === "active" ? (
                  <Button
                    variant="secondary"
                    size="sm"
                    aria-label={`Disable ${name}`}
                    onClick={() => actions.transition("disable", connection)}
                  >
                    Disable
                  </Button>
                ) : (
                  <Button
                    variant="secondary"
                    size="sm"
                    aria-label={`Enable ${name}`}
                    onClick={() => actions.transition("enable", connection)}
                  >
                    Enable
                  </Button>
                )}
                <Button
                  variant="secondary"
                  size="sm"
                  aria-label={`Archive ${name}`}
                  onClick={() => actions.transition("archive", connection)}
                >
                  Archive
                </Button>
              </>
            )}
          </div>
        </Td>
      ) : null}
    </tr>
  );
}
