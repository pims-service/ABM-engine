"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ListSkeleton } from "@/components/ListSkeleton";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button, buttonStyles } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { EmptyState } from "@/components/ui/EmptyState";
import { Menu } from "@/components/ui/Menu";
import { Modal } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { Table, Td, Th } from "@/components/ui/Table";
import { useAccess } from "@/features/access/AccessProvider";
import { useSelection } from "@/features/selection/SelectionProvider";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { errorMessage } from "@/lib/format";

import {
  type Connection,
  type ConnectionCategory,
  useConnections,
} from "./api";
import { categoryLabel } from "./categories";
import { ConnectionStatusBadge, LastTest } from "./ConnectionBadges";
import { HintList } from "./CredentialsDialog";
import {
  GROUPS,
  PLANNED,
  type ProviderGroup,
  type ProviderPreset,
  PROVIDERS,
} from "./presets";
import { type TestState, useConnectionActions } from "./useConnectionActions";

/** `/settings/integrations`: the selected client's provider connections. */
export function IntegrationsPage() {
  const { clientId, client, loading } = useSelection();
  if (!clientId) {
    return (
      <>
        <PageHeader title="Integrations" />
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

type TileStatus =
  "none" | "connected" | "partial" | "error" | "disabled" | "archived" | "soon";

/** One tile: a provider with an adapter, or a custom provider that has connections. */
interface Tile {
  key: string;
  label: string;
  tagline: string;
  group: ProviderGroup | null;
  categories: readonly ConnectionCategory[];
  preset: ProviderPreset | null;
  /** Researched but not built: shown with a badge and no Connect button. */
  planned: boolean;
  logo?: { src: string; mono?: boolean };
  connections: Connection[];
  /** Categories with an active connection. */
  connectedCategories: ConnectionCategory[];
  status: TileStatus;
}

function buildTiles(connections: Connection[]): Tile[] {
  const live = (list: Connection[]) =>
    list.filter((c) => c.archived_at === null);
  const make = (
    key: string,
    label: string,
    tagline: string,
    group: ProviderGroup | null,
    categories: readonly ConnectionCategory[],
    preset: ProviderPreset | null,
    logo?: { src: string; mono?: boolean },
  ): Tile => {
    const own = connections.filter((c) => c.provider === key);
    const active = live(own);
    const connectedCategories = [...new Set(active.map((c) => c.category))];
    let status: TileStatus = own.length > 0 ? "archived" : "none";
    if (active.length > 0) {
      if (active.some((c) => c.status === "error")) status = "error";
      else if (active.every((c) => c.status === "disabled"))
        status = "disabled";
      else if (categories.some((c) => !connectedCategories.includes(c)))
        status = "partial";
      else status = "connected";
    }
    return {
      key,
      label,
      tagline,
      group,
      categories,
      preset,
      planned: false,
      logo,
      connections: own,
      connectedCategories,
      status,
    };
  };

  const tiles = PROVIDERS.map((p) =>
    make(p.key, p.label, p.tagline, p.group, p.categories, p, p.logo),
  );
  for (const planned of PLANNED) {
    tiles.push({
      key: planned.key,
      label: planned.label,
      tagline: planned.tagline,
      group: planned.group,
      categories: planned.categories,
      preset: null,
      planned: true,
      logo: planned.logo,
      connections: [],
      connectedCategories: [],
      status: "soon",
    });
  }
  const known = new Set(PROVIDERS.map((p) => p.key));
  const custom = [...new Set(connections.map((c) => c.provider))].filter(
    (key) => !known.has(key),
  );
  for (const key of custom) {
    const own = connections.filter((c) => c.provider === key);
    const categories = [...new Set(own.map((c) => c.category))];
    tiles.push(
      make(
        key,
        key,
        `Custom · ${categories.map(categoryLabel).join(", ")}`,
        null,
        categories,
        null,
      ),
    );
  }
  return tiles;
}

const STATUS_BADGE: Record<
  TileStatus,
  { tone: "success" | "warning" | "danger" | "neutral"; label: string }
> = {
  none: { tone: "neutral", label: "Not connected" },
  connected: { tone: "success", label: "Connected" },
  partial: { tone: "warning", label: "Partly connected" },
  error: { tone: "danger", label: "Needs attention" },
  disabled: { tone: "neutral", label: "Disabled" },
  archived: { tone: "neutral", label: "Archived" },
  soon: { tone: "neutral", label: "Coming soon" },
};

function initials(label: string): string {
  const words = label.split(/[\s_-]+/).filter(Boolean);
  return (
    words.length > 1 ? words[0]![0]! + words[1]![0]! : label.slice(0, 2)
  ).toUpperCase();
}

/** The provider's logo, or its initials when it has none (a custom provider). */
function Logo({ tile }: { tile: Tile }) {
  const logo = tile.logo;
  const box = "grid h-10 w-10 shrink-0 place-items-center rounded-md";
  if (!logo) {
    return (
      <span
        aria-hidden="true"
        className={cn(box, "bg-surface-muted text-sm font-semibold")}
      >
        {initials(tile.label)}
      </span>
    );
  }
  if (logo.mono) {
    // A single-color mark drawn in the text color, so it stays visible on the dark theme.
    const mask = `url(${logo.src})`;
    return (
      <span aria-hidden="true" className={cn(box, "bg-surface-muted")}>
        <span
          className="h-6 w-6 bg-fg"
          style={{
            maskImage: mask,
            WebkitMaskImage: mask,
            maskSize: "contain",
            WebkitMaskSize: "contain",
            maskRepeat: "no-repeat",
            WebkitMaskRepeat: "no-repeat",
            maskPosition: "center",
            WebkitMaskPosition: "center",
          }}
        />
      </span>
    );
  }
  return (
    <span
      aria-hidden="true"
      className={cn(box, "bg-white p-1.5 ring-1 ring-line")}
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- a small static logo */}
      <img src={logo.src} alt="" className="h-full w-full object-contain" />
    </span>
  );
}

function ProviderTile({
  tile,
  canManage,
  onOpen,
}: {
  tile: Tile;
  canManage: boolean;
  onOpen: () => void;
}) {
  const badge = STATUS_BADGE[tile.status];
  const connected = tile.connections.length > 0;
  return (
    <li className="flex">
      <div className="flex w-full flex-col rounded-lg border border-line bg-surface-raised p-4 transition-colors hover:border-line-strong">
        <div className="flex items-start gap-3">
          <Logo tile={tile} />
          <div className="min-w-0">
            <h3 className="truncate text-sm font-semibold">{tile.label}</h3>
            <p className="line-clamp-2 text-xs text-fg-muted">{tile.tagline}</p>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {tile.categories.map((category) => (
            <span
              key={category}
              className="rounded-full bg-surface-muted px-2 py-0.5 text-xs text-fg-muted"
            >
              {categoryLabel(category)}
            </span>
          ))}
        </div>
        <div className="mt-auto flex items-center justify-between gap-2 pt-4">
          <Badge tone={badge.tone}>{badge.label}</Badge>
          {!tile.planned && (connected || canManage) ? (
            <Button
              size="sm"
              variant={connected ? "secondary" : "primary"}
              aria-label={`${connected ? "Manage" : "Connect"} ${tile.label}`}
              onClick={onOpen}
            >
              {connected ? "Manage" : "Connect"}
            </Button>
          ) : null}
        </div>
      </div>
    </li>
  );
}

type Filter = "all" | "connected" | ProviderGroup;

/** The connections of one client, shown as provider tiles with search and filters. */
export function ClientIntegrations({
  clientId,
  clientName,
}: {
  clientId: string;
  clientName: string;
}) {
  const access = useAccess();
  const [showArchived, setShowArchived] = useState(false);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [managing, setManaging] = useState<string | null>(null);
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
        <PageHeader title="Integrations" />
        <EmptyState
          title="No access to integrations"
          description={`Integrations hold provider credentials, so only managers and admins of ${clientName} can open this page.`}
        />
      </>
    );
  }

  const canManage = access.can("manage", clientId);
  const connections = data?.results ?? [];
  const tiles = buildTiles(connections);
  const needle = query.trim().toLowerCase();
  const shown = tiles.filter((tile) => {
    if (
      filter === "connected" &&
      ["none", "archived", "soon"].includes(tile.status)
    )
      return false;
    if (filter !== "all" && filter !== "connected" && tile.group !== filter)
      return false;
    if (!needle) return true;
    return [
      tile.label,
      tile.tagline,
      tile.key,
      ...tile.categories.map(categoryLabel),
    ]
      .join(" ")
      .toLowerCase()
      .includes(needle);
  });
  const open = tiles.find((tile) => tile.key === managing) ?? null;

  function openTile(tile: Tile) {
    if (tile.connections.length === 0) {
      if (tile.preset) actions.connect(tile.key, tile.connectedCategories);
      else actions.create();
    } else {
      setManaging(tile.key);
    }
  }

  const chips: { id: Filter; label: string }[] = [
    { id: "all", label: "All" },
    { id: "connected", label: "Connected" },
    ...GROUPS,
  ];

  return (
    <>
      <PageHeader
        title="Integrations"
        actions={
          <>
            <Link
              href="/settings/usage"
              className={buttonStyles({ variant: "secondary" })}
            >
              AI usage and limits
            </Link>
            {canManage ? (
              <Button variant="secondary" onClick={() => actions.create()}>
                Custom connection
              </Button>
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

      <div className="mb-5 flex flex-wrap items-center gap-3">
        <div className="min-w-56 flex-1">
          <input
            type="search"
            aria-label="Search integrations"
            placeholder="Search integrations"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="w-full rounded-md border border-line-strong bg-surface-raised px-3 py-2 text-sm"
          />
        </div>
        <div
          role="group"
          aria-label="Filter integrations"
          className="flex flex-wrap gap-1.5"
        >
          {chips.map((chip) => (
            <button
              key={chip.id}
              type="button"
              aria-pressed={filter === chip.id}
              onClick={() => setFilter(chip.id)}
              className={cn(
                "rounded-full border px-3 py-1 text-sm transition-colors",
                filter === chip.id
                  ? "border-accent bg-accent text-accent-fg"
                  : "border-line bg-surface-raised hover:border-line-strong hover:bg-surface-muted",
              )}
            >
              {chip.label}
            </button>
          ))}
        </div>
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
        <div className="flex flex-col gap-4" aria-busy={loading}>
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
          {shown.length === 0 ? (
            <EmptyState
              title="No integrations match"
              description="Try a different search or filter."
            />
          ) : (
            <ul
              aria-label="Integrations"
              className={cn(
                "grid gap-4 sm:grid-cols-2 lg:grid-cols-3",
                loading && "opacity-70",
              )}
            >
              {shown.map((tile) => (
                <ProviderTile
                  key={tile.key}
                  tile={tile}
                  canManage={canManage}
                  onOpen={() => openTile(tile)}
                />
              ))}
            </ul>
          )}
        </div>
      )}

      <Modal
        open={open !== null}
        onClose={() => setManaging(null)}
        title={open ? `${open.label} connections` : "Connections"}
        className="max-w-5xl"
      >
        {open ? (
          <div className="flex flex-col gap-4">
            {open.connections.length === 0 ? (
              <p className="text-sm text-fg-muted">
                Nothing connected. Turn on Show archived to see old ones.
              </p>
            ) : (
              <ConnectionTable
                items={open.connections}
                canManage={canManage}
                actions={actions}
              />
            )}
            <div className="flex flex-wrap justify-between gap-2">
              {canManage &&
              open.preset &&
              open.categories.some(
                (c) => !open.connectedCategories.includes(c),
              ) ? (
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    setManaging(null);
                    actions.connect(open.key, open.connectedCategories);
                  }}
                >
                  Connect{" "}
                  {open.categories
                    .filter((c) => !open.connectedCategories.includes(c))
                    .map(categoryLabel)
                    .join(" and ")}
                </Button>
              ) : (
                <span />
              )}
              <Button variant="secondary" onClick={() => setManaging(null)}>
                Close
              </Button>
            </div>
          </div>
        ) : null}
      </Modal>
      {actions.dialogs}
    </>
  );
}

function ConnectionTable({
  items,
  canManage,
  actions,
}: {
  items: Connection[];
  canManage: boolean;
  actions: ReturnType<typeof useConnectionActions>;
}) {
  return (
    <Table caption="Connections">
      <thead>
        <tr>
          <Th>Connection</Th>
          <Th>Category</Th>
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
      <Td className="text-fg-muted">{categoryLabel(connection.category)}</Td>
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
                <Menu
                  label="⋯"
                  size="sm"
                  ariaLabel={`More actions for ${name}`}
                  panelClassName="w-44"
                  groups={[
                    {
                      items: [
                        {
                          label: "Credentials",
                          onSelect: () => actions.credentials(connection),
                        },
                        connection.status === "active"
                          ? {
                              label: "Disable",
                              onSelect: () =>
                                actions.transition("disable", connection),
                            }
                          : {
                              label: "Enable",
                              onSelect: () =>
                                actions.transition("enable", connection),
                            },
                        {
                          label: "Archive",
                          onSelect: () =>
                            actions.transition("archive", connection),
                        },
                      ],
                    },
                  ]}
                />
              </>
            )}
          </div>
        </Td>
      ) : null}
    </tr>
  );
}
