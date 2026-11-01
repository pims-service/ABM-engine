"use client";

import Link from "next/link";
import { type FormEvent, type ReactNode, useEffect, useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button, buttonStyles } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { Menu } from "@/components/ui/Menu";
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
  type AccountSummary,
  analyzeSelected,
  analyzeUnassessed,
  decideMany,
  EMPTY_FILTERS,
  exportApproved,
  findAndVerifyEmails,
  getAccountSummary,
  listAccounts,
  stageLabel,
} from "./accountsApi";
import { addCompany } from "./api";
import { pushApprovedToCrm, queueApprovedToCrm } from "./crmApi";

/** The message after queueing background work: what started, then what was left out and why. */
function queuedNotice(
  result: { queued: number; already_running: number; not_ready: number },
  [verb, one, many, tail = ""]: [string, string, string, string?],
  after: string,
  whySkipped: string,
): string {
  const parts: string[] = [];
  if (result.queued > 0) {
    const noun = result.queued === 1 ? one : many;
    parts.push(`${verb} ${result.queued} ${noun}${tail ? ` ${tail}` : ""}.`);
    if (after) parts.push(after);
  } else {
    parts.push("Nothing new to do.");
  }
  if (result.already_running > 0) {
    const verbIs = result.already_running === 1 ? "was" : "were";
    parts.push(`${result.already_running} ${verbIs} already in progress.`);
  }
  if (result.not_ready > 0) {
    parts.push(`${result.not_ready} skipped because ${whySkipped}.`);
  }
  return parts.join(" ");
}

const POLL_MS = 4000;
const PAGE_SIZE = 50;

const TRIGGER_TYPES = [
  ["sales_hiring", "Sales hiring"],
  ["bd_hiring", "Business development hiring"],
  ["commercial_hiring", "Commercial hiring"],
  ["partnerships_hiring", "Partnerships hiring"],
  ["headcount_growth", "Headcount growth"],
  ["commercial_team_growth", "Commercial team growth"],
  ["funding", "Funding"],
  ["market_expansion", "Market expansion"],
  ["new_leadership", "New leadership"],
  ["new_office", "New office"],
  ["new_product", "New product"],
  ["major_partnership", "Major partnership"],
  ["other", "Other"],
] as const;

type SelectKey = "icp_fit" | "trigger" | "ai" | "human" | "outreach" | "review";

/** The dropdown filters, grouped the way people think about an account. */
const FILTER_GROUPS: {
  heading: string;
  selects: { key: SelectKey; label: string; options: [string, string][] }[];
}[] = [
  {
    heading: "Fit",
    selects: [
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
    ],
  },
  {
    heading: "Decision",
    selects: [
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
        key: "human",
        label: "Your decision",
        options: [
          ["add", "Add"],
          ["hold", "Hold"],
          ["skip", "Skip"],
          ["undecided", "Undecided"],
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
    ],
  },
  {
    heading: "Outreach",
    selects: [
      {
        key: "outreach",
        label: "Outreach",
        options: [
          ["none", "Not started"],
          ["contacted", "Contacted"],
          ["replied", "Replied"],
          ["meeting_booked", "Meeting booked"],
        ],
      },
    ],
  },
];

/** Every filter that can show as a removable chip, with the text to show for its value. */
function activeChips(
  filters: AccountFilters,
): { key: keyof AccountFilters; text: string }[] {
  const chips: { key: keyof AccountFilters; text: string }[] = [];
  for (const group of FILTER_GROUPS) {
    for (const { key, label, options } of group.selects) {
      const value = filters[key];
      if (!value) continue;
      const text = options.find(([v]) => v === value)?.[1] ?? value;
      chips.push({ key, text: `${label}: ${text}` });
    }
  }
  const trigger = TRIGGER_TYPES.find(([v]) => v === filters.trigger_type);
  if (filters.trigger_type) {
    chips.push({
      key: "trigger_type",
      text: `Trigger type: ${trigger?.[1] ?? filters.trigger_type}`,
    });
  }
  if (filters.industry)
    chips.push({ key: "industry", text: `Industry: ${filters.industry}` });
  if (filters.country)
    chips.push({ key: "country", text: `Country: ${filters.country}` });
  if (filters.size_min || filters.size_max) {
    chips.push({
      key: filters.size_min ? "size_min" : "size_max",
      text: `Size: ${filters.size_min || "0"}–${filters.size_max || "any"}`,
    });
  }
  if (filters.buyer_title)
    chips.push({ key: "buyer_title", text: `Buyer: ${filters.buyer_title}` });
  return chips;
}

/** A saved view: a named set of filters. Picking one replaces the current filters. */
const VIEWS: {
  label: string;
  filters: Partial<AccountFilters>;
  count?: (c: AccountSummary) => number;
}[] = [
  { label: "All", filters: {}, count: (c) => c.total },
  {
    label: "Needs review",
    filters: { review: "needs_review" },
    count: (c) => c.needs_review,
  },
  {
    label: "Strong fit",
    filters: { icp_fit: "strong" },
    count: (c) => c.strong,
  },
  {
    label: "Has trigger",
    filters: { trigger: "yes" },
    count: (c) => c.trigger_yes,
  },
  {
    label: "Not analyzed",
    filters: { icp_fit: "unassessed" },
    count: (c) => c.not_assessed,
  },
];

const FILTER_KEYS = Object.keys(EMPTY_FILTERS).filter(
  (k) => k !== "page" && k !== "ordering" && k !== "search",
) as (keyof AccountFilters)[];

function isView(
  filters: AccountFilters,
  view: Partial<AccountFilters>,
): boolean {
  return FILTER_KEYS.every((k) => filters[k] === (view[k] ?? ""));
}

/** One choice of a filter: click to pick it, click again to clear it. */
function OptionPill({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "rounded-full border px-3 py-1 text-sm transition-colors",
        active
          ? "border-accent bg-accent text-accent-fg"
          : "border-line bg-surface-raised text-fg hover:border-line-strong hover:bg-surface-muted",
      )}
    >
      {children}
    </button>
  );
}

function ViewTab({
  label,
  value,
  active,
  onClick,
}: {
  label: string;
  value?: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "-mb-px whitespace-nowrap border-b-2 px-1 pb-2 text-sm",
        active
          ? "border-fg font-medium"
          : "border-transparent text-fg-muted hover:text-fg",
      )}
    >
      {label} <span className="text-fg-muted">{value ?? "…"}</span>
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
  const [showFilters, setShowFilters] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
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
  const listKey = JSON.stringify({ ...filters, page: 0, campaignId });
  useEffect(() => {
    setSelected(new Set());
  }, [listKey]);
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
        <PageHeader title="Companies" />
        <EmptyState
          title="No campaign selected"
          description="Choose a client and a campaign in the switcher at the top."
        />
      </>
    );
  }

  const counts = summary.data;
  const unassessed = counts?.not_assessed ?? 0;
  const chips = activeChips(filters);
  const filtered = chips.length > 0 || filters.search !== "";

  function openView(view: Partial<AccountFilters>) {
    setTyped({ search: "", industry: "" });
    setFilters({ ...EMPTY_FILTERS, ordering: filters.ordering, ...view });
  }

  function removeChip(name: keyof AccountFilters) {
    if (name === "industry") setTyped({ ...typed, industry: "" });
    if (name === "size_min" || name === "size_max") {
      setFilters((current) => ({
        ...current,
        size_min: "",
        size_max: "",
        page: 1,
      }));
      return;
    }
    set(name, "" as never);
  }

  const pageIds = rows.data?.results.map((row) => row.id) ?? [];
  const allOnPage =
    pageIds.length > 0 && pageIds.every((rowId) => selected.has(rowId));
  const someOnPage = pageIds.some((rowId) => selected.has(rowId));

  function toggleRow(rowId: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (!next.delete(rowId)) next.add(rowId);
      return next;
    });
  }

  function togglePage() {
    setSelected((current) => {
      const next = new Set(current);
      for (const rowId of pageIds) {
        if (allOnPage) next.delete(rowId);
        else next.add(rowId);
      }
      return next;
    });
  }

  async function bulkDecide(choice: "add" | "hold" | "skip") {
    setBulkBusy(true);
    try {
      const result = await decideMany(campaign!.id, [...selected], choice);
      const parts = [`Marked ${result.saved} as ${choice.toUpperCase()}.`];
      if (result.unchanged > 0) parts.push(`${result.unchanged} already were.`);
      if (result.failed > 0) parts.push(`${result.failed} could not be saved.`);
      setNotice(parts.join(" "));
      setSelected(new Set());
      refresh();
    } catch (cause) {
      setNotice(`Could not save the decisions. ${errorMessage(cause)}`);
    } finally {
      setBulkBusy(false);
    }
  }

  async function bulkAnalyze() {
    const count = selected.size;
    if (
      !window.confirm(
        `Analyze ${count} selected ${count === 1 ? "company" : "companies"}? Each one is judged by the AI provider, which costs money per company.`,
      )
    )
      return;
    setBulkBusy(true);
    try {
      const result = await analyzeSelected(campaign!.id, [...selected]);
      setNotice(
        `Queued ${result.queued} ${result.queued === 1 ? "company" : "companies"}${
          result.already_running
            ? ` (${result.already_running} already running)`
            : ""
        }. Results appear here as they finish.`,
      );
      setSelected(new Set());
      refresh();
    } catch (cause) {
      setNotice(`Could not queue the analysis. ${errorMessage(cause)}`);
    } finally {
      setBulkBusy(false);
    }
  }

  async function pushNow() {
    if (
      !window.confirm(
        "Push every account you decided ADD to the CRM? Accounts that are not ready are skipped and listed.",
      )
    )
      return;
    try {
      const result = await pushApprovedToCrm(campaign!.id);
      const skipped = result.skipped
        .map((s) => `${s.company} (${s.reason})`)
        .join("; ");
      setNotice(
        `Sent ${result.pushed} to the CRM.${skipped ? ` Skipped: ${skipped}` : ""}`,
      );
      refresh();
    } catch (cause) {
      setNotice(`Could not push. ${errorMessage(cause)}`);
    }
  }

  async function findEmails() {
    if (
      !window.confirm(
        "Look up and check the emails of the primary and secondary contacts of every approved account? It runs in the background and uses provider credits.",
      )
    )
      return;
    try {
      const result = await findAndVerifyEmails(campaign!.id);
      setNotice(
        queuedNotice(
          result,
          ["Checking emails for", "person", "people"],
          "Results show up on each account as they come in.",
          "there was nothing to look up or check",
        ),
      );
    } catch (cause) {
      setNotice(`Could not queue. ${errorMessage(cause)}`);
    }
  }

  async function queuePush() {
    if (
      !window.confirm(
        "Send every approved account to the CRM in the background? Accounts that are not ready yet are skipped.",
      )
    )
      return;
    try {
      const result = await queueApprovedToCrm(campaign!.id);
      setNotice(
        queuedNotice(
          result,
          ["Sending", "account", "accounts", "to the CRM"],
          "",
          "they still need a primary contact with an email and an approved message",
        ),
      );
    } catch (cause) {
      setNotice(`Could not queue. ${errorMessage(cause)}`);
    }
  }

  return (
    <>
      <PageHeader
        title="Companies"
        description={`Target accounts of ${campaign.name}.`}
        actions={
          <>
            <Button variant="secondary" onClick={() => setAdding(!adding)}>
              Add company
            </Button>
            <Menu
              label="More ▾"
              groups={[
                {
                  heading: "Bring in",
                  items: [{ label: "Import a CSV", href: "/companies/import" }],
                },
                {
                  heading: "Review",
                  items: [
                    {
                      label: "Review queue",
                      description: "Decide on accounts one by one",
                      href: "/companies/review",
                    },
                    {
                      label: "AI vs you",
                      description: "How often your decision matches the AI",
                      href: "/companies/agreement",
                    },
                  ],
                },
                {
                  heading: "Outreach and CRM",
                  items: [
                    {
                      label: "Find and verify emails",
                      description: "Approved accounts, uses provider credits",
                      onSelect: findEmails,
                    },
                    { label: "Push to CRM now", onSelect: pushNow },
                    {
                      label: "Queue CRM push",
                      description: "Runs in the background",
                      onSelect: queuePush,
                    },
                    {
                      label: "Export approved",
                      onSelect: () =>
                        exportApproved(campaign.id).catch((cause) =>
                          setNotice(`Could not export. ${errorMessage(cause)}`),
                        ),
                    },
                  ],
                },
              ]}
            />
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

      <nav
        aria-label="Views"
        className="mb-4 flex flex-wrap items-center gap-x-5 gap-y-1 border-b border-line"
      >
        {VIEWS.map((view) => (
          <ViewTab
            key={view.label}
            label={view.label}
            value={counts ? view.count?.(counts) : undefined}
            active={isView(filters, view.filters)}
            onClick={() => openView(view.filters)}
          />
        ))}
        {(counts?.analyzing ?? 0) > 0 ? (
          <span className="-mb-px pb-2 text-sm text-fg-muted">
            Analyzing {counts?.analyzing}…
          </span>
        ) : null}
      </nav>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="min-w-56 flex-1">
          <input
            type="search"
            aria-label="Search companies"
            placeholder="Search by name or website"
            value={typed.search}
            onChange={(e) => setTyped({ ...typed, search: e.target.value })}
            className="w-full rounded-md border border-line-strong bg-surface-raised px-3 py-1.5 text-sm"
          />
        </div>
        <Button
          variant="secondary"
          aria-expanded={showFilters}
          onClick={() => setShowFilters(!showFilters)}
        >
          Filters{chips.length > 0 ? ` (${chips.length})` : ""}
        </Button>
        <select
          aria-label="Sort"
          value={filters.ordering}
          onChange={(e) => set("ordering", e.target.value)}
          className="rounded-md border border-line-strong bg-surface-raised px-3 py-1.5 text-sm"
        >
          <option value="">Sort: Name</option>
          <option value="-icp_fit">Sort: Best fit first</option>
          <option value="-updated_at">Sort: Recently updated</option>
        </select>
      </div>

      {showFilters ? (
        <Card className="mb-3">
          <div className="grid gap-x-10 gap-y-6 lg:grid-cols-2">
            {[
              FILTER_GROUPS.filter((g) => g.heading !== "Decision"),
              FILTER_GROUPS.filter((g) => g.heading === "Decision"),
            ].map((column, index) => (
              <div key={index} className="flex min-w-0 flex-col gap-6">
                {column.map((group) => (
                  <section key={group.heading} className="min-w-0">
                    <h3 className="mb-3 text-xs font-medium uppercase tracking-wide text-fg-muted">
                      {group.heading}
                    </h3>
                    <div className="flex flex-col gap-3">
                      {group.selects.map(({ key: name, label, options }) => (
                        <div
                          key={name}
                          role="group"
                          aria-label={label}
                          className="flex flex-wrap items-center gap-x-4 gap-y-1.5"
                        >
                          <span className="w-28 shrink-0 text-sm font-medium">
                            {label}
                          </span>
                          <div className="flex flex-wrap gap-1.5">
                            {options.map(([value, text]) => (
                              <OptionPill
                                key={value}
                                active={filters[name] === value}
                                onClick={() =>
                                  set(
                                    name,
                                    filters[name] === value ? "" : value,
                                  )
                                }
                              >
                                {text}
                              </OptionPill>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  </section>
                ))}
              </div>
            ))}
          </div>

          <section className="mt-6 border-t border-line pt-5">
            <h3 className="mb-3 text-xs font-medium uppercase tracking-wide text-fg-muted">
              Company
            </h3>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <TextField
                label="Industry"
                value={typed.industry}
                onChange={(e) =>
                  setTyped({ ...typed, industry: e.target.value })
                }
              />
              <TextField
                label="Country"
                placeholder="SA"
                maxLength={2}
                value={filters.country}
                onChange={(e) => set("country", e.target.value.toUpperCase())}
              />
              <TextField
                label="Buyer title"
                value={filters.buyer_title}
                onChange={(e) => set("buyer_title", e.target.value)}
              />
              <TextField
                label="Employees from"
                inputMode="numeric"
                value={filters.size_min}
                onChange={(e) =>
                  set("size_min", e.target.value.replace(/\D/g, ""))
                }
              />
              <TextField
                label="Employees to"
                inputMode="numeric"
                value={filters.size_max}
                onChange={(e) =>
                  set("size_max", e.target.value.replace(/\D/g, ""))
                }
              />
              <label className="flex min-w-0 flex-col gap-1.5 text-sm font-medium">
                Trigger type
                <select
                  value={filters.trigger_type}
                  onChange={(e) => set("trigger_type", e.target.value)}
                  className="w-full rounded-md border border-line-strong bg-surface-raised px-3 py-2 text-sm font-normal"
                >
                  <option value="">Any</option>
                  {TRIGGER_TYPES.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </section>

          <div className="mt-5 flex items-center justify-between gap-3 border-t border-line pt-4">
            <button
              type="button"
              onClick={() => openView({})}
              disabled={!filtered}
              className="text-sm text-fg-muted underline-offset-2 hover:underline disabled:opacity-50 disabled:hover:no-underline"
            >
              Clear all filters
            </button>
            <Button size="sm" onClick={() => setShowFilters(false)}>
              Done
            </Button>
          </div>
        </Card>
      ) : null}

      {filtered ? (
        <div
          className="mb-4 flex flex-wrap items-center gap-2"
          aria-label="Active filters"
        >
          {chips.map((chip) => (
            <button
              key={chip.text}
              type="button"
              onClick={() => removeChip(chip.key)}
              aria-label={`Remove filter ${chip.text}`}
              className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface-muted px-2.5 py-1 text-xs hover:border-line-strong"
            >
              {chip.text} <span aria-hidden="true">×</span>
            </button>
          ))}
          <button
            type="button"
            onClick={() => openView({})}
            className="text-xs text-fg-muted underline-offset-2 hover:underline"
          >
            Clear all
          </button>
        </div>
      ) : null}

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
                <Th className="w-10">
                  <input
                    type="checkbox"
                    aria-label="Select all companies on this page"
                    className="h-4 w-4 accent-accent"
                    checked={allOnPage}
                    ref={(el) => {
                      if (el) el.indeterminate = someOnPage && !allOnPage;
                    }}
                    onChange={togglePage}
                  />
                </Th>
                <Th>Company</Th>
                <Th>Fit</Th>
                <Th>AI / You</Th>
                <Th>Primary buyer</Th>
                <Th>Outreach</Th>
              </tr>
            </thead>
            <tbody>
              {rows.data!.results.map((row) => {
                const details = [
                  row.domain,
                  row.country,
                  row.industry,
                  row.employee_count ? `${row.employee_count} people` : null,
                ].filter(Boolean);
                return (
                  <tr
                    key={row.id}
                    className={selected.has(row.id) ? "bg-surface-muted" : ""}
                  >
                    <Td>
                      <input
                        type="checkbox"
                        aria-label={`Select ${row.name}`}
                        className="h-4 w-4 accent-accent"
                        checked={selected.has(row.id)}
                        onChange={() => toggleRow(row.id)}
                      />
                    </Td>
                    <Td>
                      <Link
                        href={`/companies/${row.id}`}
                        className="font-medium underline-offset-2 hover:underline"
                      >
                        {row.name}
                      </Link>
                      <span className="block text-xs text-fg-muted">
                        {details.length > 0 ? details.join(" · ") : "—"}
                      </span>
                    </Td>
                    <Td>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Fit row={row} />
                        {row.icp_fit && row.trigger ? (
                          <StatusPill kind="trigger" value="yes" />
                        ) : null}
                      </div>
                      {row.trigger_types.length > 0 ? (
                        <span className="mt-1 block text-xs text-fg-muted">
                          {row.trigger_types.slice(0, 2).join(", ")}
                        </span>
                      ) : null}
                    </Td>
                    <Td>
                      <div className="flex flex-wrap items-center gap-1.5">
                        {row.ai_recommendation ? (
                          <StatusPill
                            kind="ai"
                            value={
                              row.ai_recommendation as "add" | "hold" | "skip"
                            }
                          />
                        ) : (
                          <span className="text-fg-muted">—</span>
                        )}
                        <Decision row={row} />
                      </div>
                    </Td>
                    <Td className="text-fg-muted">
                      {row.primary_buyer ?? "—"}
                    </Td>
                    <Td className="text-fg-muted">
                      {row.outreach_status === "none"
                        ? "—"
                        : stageLabel(row.outreach_status)}
                    </Td>
                  </tr>
                );
              })}
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

      {selected.size > 0 ? (
        <div
          role="region"
          aria-label="Bulk actions"
          className="fixed inset-x-4 bottom-4 z-20 mx-auto flex w-fit max-w-full flex-wrap items-center gap-3 rounded-lg border border-line bg-surface-raised px-4 py-3 shadow-lg"
        >
          <span className="text-sm font-medium">{selected.size} selected</span>
          {(["add", "hold", "skip"] as const).map((choice) => (
            <Button
              key={choice}
              size="sm"
              variant={choice === "add" ? "primary" : "secondary"}
              disabled={bulkBusy}
              onClick={() => bulkDecide(choice)}
            >
              {choice.toUpperCase()}
            </Button>
          ))}
          <Button
            size="sm"
            variant="secondary"
            disabled={bulkBusy}
            onClick={bulkAnalyze}
          >
            Analyze
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={bulkBusy}
            onClick={() => setSelected(new Set())}
          >
            Clear
          </Button>
        </div>
      ) : null}

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
