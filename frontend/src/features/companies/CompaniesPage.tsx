"use client";

import Link from "next/link";
import { type FormEvent, useEffect, useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button, buttonStyles } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Pagination } from "@/components/ui/Pagination";
import { Skeleton } from "@/components/ui/Skeleton";
import { StatusPill } from "@/components/ui/StatusPill";
import { Table, Td, Th } from "@/components/ui/Table";
import { TextField } from "@/components/ui/TextField";
import { useOptionalSelection } from "@/features/selection/SelectionProvider";
import { cn } from "@/lib/cn";
import { errorMessage } from "@/lib/format";
import { useApiQuery } from "@/lib/hooks/useApiQuery";
import { useDebouncedValue } from "@/lib/hooks/useDebouncedValue";

import {
  type AccountFilters,
  type AccountRow,
  analyzeUnassessed,
  EMPTY_FILTERS,
  getAccountSummary,
  listAccounts,
} from "./accountsApi";
import { addCompany } from "./api";

const POLL_MS = 4000;
const PAGE_SIZE = 50;

const SELECTS = [
  {
    key: "icp_fit",
    label: "ICP fit",
    options: [
      ["strong", "Strong"],
      ["medium", "Medium"],
      ["weak", "Weak"],
      ["unassessed", "Not analyzed"],
    ],
  },
  {
    key: "trigger",
    label: "Trigger",
    options: [
      ["yes", "Yes"],
      ["no", "No"],
    ],
  },
  {
    key: "ai",
    label: "AI says",
    options: [
      ["add", "Add"],
      ["hold", "Hold"],
      ["skip", "Skip"],
    ],
  },
  {
    key: "review",
    label: "Review",
    options: [
      ["needs_review", "Needs review"],
      ["decided", "Decided"],
    ],
  },
] as const;

function Tile({
  label,
  value,
  active,
  onClick,
}: {
  label: string;
  value: number | string;
  active?: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "rounded-lg border bg-surface-raised p-4 text-left transition-colors hover:bg-surface-muted",
        active ? "border-accent" : "border-line",
      )}
    >
      <span className="block text-xs font-medium text-fg-muted">{label}</span>
      <span className="mt-1 block text-xl font-semibold tracking-tight">
        {value}
      </span>
    </button>
  );
}

function Decision({ row }: { row: AccountRow }) {
  if (row.human_decision) {
    return (
      <Badge tone={row.human_decision === "skip" ? "neutral" : "info"}>
        {row.human_decision.toUpperCase()}
      </Badge>
    );
  }
  return row.review === "needs_review" ? (
    <span className="text-xs font-medium text-warning-fg">Needs review</span>
  ) : (
    <span className="text-fg-muted">—</span>
  );
}

function Fit({ row }: { row: AccountRow }) {
  const active =
    row.analysis?.status === "queued" || row.analysis?.status === "running";
  if (active) return <Badge tone="info">Analyzing…</Badge>;
  if (row.icp_fit) {
    return (
      <StatusPill
        kind="icp"
        value={row.icp_fit as "strong" | "medium" | "weak"}
      />
    );
  }
  if (row.analysis?.status === "failed") {
    return <Badge tone="warning">Failed</Badge>;
  }
  return <span className="text-fg-muted">Not analyzed</span>;
}

/** `/companies`: the master ABM dashboard of the selected campaign (Brief section 13). */
export function CompaniesPage() {
  const selection = useOptionalSelection();
  const campaign = selection?.campaign ?? null;
  const [filters, setFilters] = useState<AccountFilters>(EMPTY_FILTERS);
  const [typed, setTyped] = useState({ search: "", industry: "" });
  const debounced = useDebouncedValue(typed, 300);
  const [adding, setAdding] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  // Typed text filters apply after a short pause, and always start at page 1.
  useEffect(() => {
    setFilters((current) =>
      current.search === debounced.search &&
      current.industry === debounced.industry
        ? current
        : { ...current, ...debounced, page: 1 },
    );
  }, [debounced]);

  const campaignId = campaign?.id ?? "none";
  const key = `accounts:${campaignId}:${JSON.stringify(filters)}`;
  const rows = useApiQuery(
    key,
    (signal) => listAccounts(campaign!.id, filters, signal),
    { enabled: campaign !== null },
  );
  const summary = useApiQuery(
    `accounts-summary:${campaignId}`,
    (signal) => getAccountSummary(campaign!.id, signal),
    { enabled: campaign !== null },
  );

  // While analyses are queued or running, look again every few seconds.
  const analyzing = summary.data?.analyzing ?? 0;
  const reloadRows = rows.reload;
  const reloadSummary = summary.reload;
  useEffect(() => {
    if (analyzing === 0) return;
    const timer = setInterval(() => {
      reloadRows();
      reloadSummary();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [analyzing, reloadRows, reloadSummary]);

  function set<K extends keyof AccountFilters>(
    name: K,
    value: AccountFilters[K],
  ) {
    setFilters((current) => ({ ...current, [name]: value, page: 1 }));
  }

  function refresh() {
    rows.reload();
    summary.reload();
  }

  if (!campaign) {
    return (
      <>
        <PageHeader
          title="Companies"
          description="Pick a client and a campaign in the header to see its companies."
        />
        <EmptyState
          title="No campaign selected"
          description="Choose a client and a campaign from the switcher in the header."
        />
      </>
    );
  }

  const counts = summary.data;
  const unassessed = counts?.not_assessed ?? 0;
  const filtered = [
    filters.search,
    filters.icp_fit,
    filters.trigger,
    filters.ai,
    filters.review,
    filters.industry,
  ].some((value) => value !== "");

  return (
    <>
      <PageHeader
        title="Companies"
        description={`Target accounts of ${campaign.name}.`}
        actions={
          <>
            <Button variant="secondary" onClick={() => setAdding(!adding)}>
              Add one
            </Button>
            <Link
              href="/companies/import"
              className={buttonStyles({ variant: "secondary" })}
            >
              Import a CSV
            </Link>
            <Button
              disabled={unassessed === 0}
              onClick={() => setConfirm(true)}
            >
              {unassessed === 0
                ? "Everything is analyzed"
                : `Analyze ${unassessed} ${unassessed === 1 ? "company" : "companies"}`}
            </Button>
          </>
        }
      />
      {notice ? (
        <Alert tone="success" className="mb-4">
          {notice}
        </Alert>
      ) : null}
      {adding ? (
        <AddCompany
          campaignId={campaign.id}
          onAdded={() => {
            setAdding(false);
            refresh();
          }}
        />
      ) : null}

      {!filtered && (counts?.needs_review ?? 0) > 0 ? (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-surface-raised px-4 py-3 text-sm">
          <span>
            <span className="font-medium">
              {counts?.needs_review}{" "}
              {counts?.needs_review === 1 ? "account is" : "accounts are"}
            </span>{" "}
            waiting for your decision.
          </span>
          <Button size="sm" onClick={() => set("review", "needs_review")}>
            Start reviewing
          </Button>
        </div>
      ) : null}

      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Tile
          label="All companies"
          value={counts?.total ?? "…"}
          active={!filtered}
          onClick={() => {
            setTyped({ search: "", industry: "" });
            setFilters(EMPTY_FILTERS);
          }}
        />
        <Tile
          label="Needs review"
          value={counts?.needs_review ?? "…"}
          active={filters.review === "needs_review"}
          onClick={() => set("review", "needs_review")}
        />
        <Tile
          label="Strong fit"
          value={counts?.strong ?? "…"}
          active={filters.icp_fit === "strong"}
          onClick={() => set("icp_fit", "strong")}
        />
        <Tile
          label="With a trigger"
          value={counts?.trigger_yes ?? "…"}
          active={filters.trigger === "yes"}
          onClick={() => set("trigger", "yes")}
        />
        <Tile
          label="Not analyzed"
          value={counts?.not_assessed ?? "…"}
          active={filters.icp_fit === "unassessed"}
          onClick={() => set("icp_fit", "unassessed")}
        />
        <Tile label="Analyzing now" value={counts?.analyzing ?? "…"} />
      </div>

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div className="min-w-40 flex-1">
          <TextField
            label="Search"
            placeholder="Name or website"
            value={typed.search}
            onChange={(e) => setTyped({ ...typed, search: e.target.value })}
          />
        </div>
        {SELECTS.map(({ key: name, label, options }) => (
          <label
            key={name}
            className="flex flex-col gap-1.5 text-sm font-medium"
          >
            {label}
            <select
              value={filters[name]}
              onChange={(e) => set(name, e.target.value)}
              className="rounded-md border border-line-strong bg-surface-raised px-3 py-1.5 text-sm font-normal"
            >
              <option value="">Any</option>
              {options.map(([value, text]) => (
                <option key={value} value={value}>
                  {text}
                </option>
              ))}
            </select>
          </label>
        ))}
        <div className="w-32">
          <TextField
            label="Industry"
            value={typed.industry}
            onChange={(e) => setTyped({ ...typed, industry: e.target.value })}
          />
        </div>
        <label className="flex flex-col gap-1.5 text-sm font-medium">
          Sort
          <select
            value={filters.ordering}
            onChange={(e) => set("ordering", e.target.value)}
            className="rounded-md border border-line-strong bg-surface-raised px-3 py-1.5 text-sm font-normal"
          >
            <option value="">Name</option>
            <option value="-icp_fit">Best fit first</option>
            <option value="-updated_at">Recently updated</option>
          </select>
        </label>
      </div>

      {rows.error ? (
        <Alert
          tone="danger"
          action={
            <Button variant="secondary" size="sm" onClick={refresh}>
              Try again
            </Button>
          }
        >
          Could not load companies. {rows.error.message}
        </Alert>
      ) : rows.loading && !rows.data ? (
        <div role="status" className="flex flex-col gap-2">
          <span className="sr-only">Loading companies…</span>
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : (rows.data?.results.length ?? 0) === 0 ? (
        <EmptyState
          title={filtered ? "No companies match" : "No companies yet"}
          description={
            filtered
              ? "Change or clear the filters."
              : "Add a company by hand or import a CSV. Then analyze them to see which ones fit and what to do next."
          }
          action={
            filtered ? undefined : (
              <div className="flex gap-2">
                <Button variant="secondary" onClick={() => setAdding(true)}>
                  Add one
                </Button>
                <Link href="/companies/import" className={buttonStyles()}>
                  Import a CSV
                </Link>
              </div>
            )
          }
        />
      ) : (
        <>
          <Table caption="Companies">
            <thead>
              <tr>
                <Th>Company</Th>
                <Th>ICP fit</Th>
                <Th>Trigger</Th>
                <Th>AI</Th>
                <Th>Decision</Th>
                <Th>Industry</Th>
                <Th>Size</Th>
                <Th>Primary buyer</Th>
              </tr>
            </thead>
            <tbody>
              {rows.data!.results.map((row) => (
                <tr key={row.id}>
                  <Td>
                    <Link
                      href={`/companies/${row.id}`}
                      className="font-medium underline-offset-2 hover:underline"
                    >
                      {row.name}
                    </Link>
                    <span className="block text-xs text-fg-muted">
                      {row.domain ?? "—"}
                      {row.country ? ` · ${row.country}` : ""}
                    </span>
                  </Td>
                  <Td>
                    <Fit row={row} />
                  </Td>
                  <Td>
                    {row.icp_fit ? (
                      <>
                        <StatusPill
                          kind="trigger"
                          value={row.trigger ? "yes" : "no"}
                        />
                        {row.trigger_types.length > 0 ? (
                          <span className="mt-1 block text-xs text-fg-muted">
                            {row.trigger_types.slice(0, 2).join(", ")}
                          </span>
                        ) : null}
                      </>
                    ) : (
                      <span className="text-fg-muted">—</span>
                    )}
                  </Td>
                  <Td>
                    {row.ai_recommendation ? (
                      <StatusPill
                        kind="ai"
                        value={row.ai_recommendation as "add" | "hold" | "skip"}
                      />
                    ) : (
                      <span className="text-fg-muted">—</span>
                    )}
                  </Td>
                  <Td>
                    <Decision row={row} />
                  </Td>
                  <Td className="text-fg-muted">{row.industry ?? "—"}</Td>
                  <Td className="text-fg-muted">{row.employee_count ?? "—"}</Td>
                  <Td className="text-fg-muted">{row.primary_buyer ?? "—"}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
          <Pagination
            page={filters.page}
            pageSize={PAGE_SIZE}
            count={rows.data!.count}
            disabled={rows.loading}
            onPageChange={(page) => setFilters({ ...filters, page })}
          />
        </>
      )}

      <ConfirmDialog
        open={confirm}
        title={`Analyze ${unassessed} companies?`}
        description="Each company is read and judged by the AI provider, which costs money per company. Companies that are already analyzing are skipped. You can keep working while it runs."
        confirmLabel="Analyze"
        tone="primary"
        onCancel={() => setConfirm(false)}
        onConfirm={async () => {
          const result = await analyzeUnassessed(campaign.id);
          setConfirm(false);
          setNotice(
            `Queued ${result.queued} ${result.queued === 1 ? "company" : "companies"}${
              result.already_running
                ? ` (${result.already_running} already running)`
                : ""
            }. Results appear here as they finish.`,
          );
          refresh();
        }}
      />
    </>
  );
}

function AddCompany({
  campaignId,
  onAdded,
}: {
  campaignId: string;
  onAdded: () => void;
}) {
  const [name, setName] = useState("");
  const [website, setWebsite] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await addCompany(campaignId, {
        name: name.trim(),
        website: website.trim(),
      });
      onAdded();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card title="Add a company" className="mb-6">
      <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
        <div className="min-w-56 flex-1">
          <TextField
            label="Company name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </div>
        <div className="min-w-56 flex-1">
          <TextField
            label="Website"
            placeholder="acme.com"
            value={website}
            onChange={(e) => setWebsite(e.target.value)}
          />
        </div>
        <Button type="submit" disabled={saving || !name.trim()}>
          {saving ? "Adding…" : "Add"}
        </Button>
      </form>
      {error ? (
        <Alert tone="danger" className="mt-3">
          {error}
        </Alert>
      ) : null}
    </Card>
  );
}
