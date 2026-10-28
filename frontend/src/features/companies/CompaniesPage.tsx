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
    key: "outreach",
    label: "Outreach",
    options: [
      ["none", "Not started"],
      ["contacted", "Contacted"],
      ["replied", "Replied"],
      ["meeting_booked", "Meeting booked"],
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

function Tab({
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
  const body = (
    <>
      {label} <span className="text-fg-muted">{value}</span>
    </>
  );
  const base = "-mb-px whitespace-nowrap border-b-2 px-1 pb-2 text-sm";
  if (!onClick) {
    return <span className={cn(base, "border-transparent")}>{body}</span>;
  }
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        base,
        active
          ? "border-fg font-medium"
          : "border-transparent text-fg-muted hover:text-fg",
      )}
    >
      {body}
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
  const filtered = [
    filters.search,
    filters.icp_fit,
    filters.trigger,
    filters.ai,
    filters.review,
    filters.industry,
    filters.human,
    filters.outreach,
    filters.country,
    filters.size_min,
    filters.size_max,
    filters.trigger_type,
    filters.buyer_title,
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
            <Button
              variant="secondary"
              onClick={async () => {
                if (
                  !window.confirm(
                    "Push every account you decided ADD to the CRM? Accounts that are not ready are skipped and listed.",
                  )
                )
                  return;
                try {
                  const result = await pushApprovedToCrm(campaign.id);
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
              }}
            >
              Push to CRM
            </Button>
            <Button
              variant="secondary"
              onClick={async () => {
                if (
                  !window.confirm(
                    "Look up and check the emails of the primary and secondary contacts of every approved account? It runs in the background and uses provider credits.",
                  )
                )
                  return;
                try {
                  const result = await findAndVerifyEmails(campaign.id);
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
              }}
            >
              Find and verify emails
            </Button>
            <Button
              variant="secondary"
              onClick={async () => {
                if (
                  !window.confirm(
                    "Send every approved account to the CRM in the background? Accounts that are not ready yet are skipped.",
                  )
                )
                  return;
                try {
                  const result = await queueApprovedToCrm(campaign.id);
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
              }}
            >
              Queue CRM push
            </Button>
            <Button
              variant="secondary"
              onClick={() =>
                exportApproved(campaign.id).catch((cause) =>
                  setNotice(`Could not export. ${errorMessage(cause)}`),
                )
              }
            >
              Export approved
            </Button>
            <Link
              href="/companies/agreement"
              className={buttonStyles({ variant: "secondary" })}
            >
              AI vs you
            </Link>
            <Link
              href="/companies/review"
              className={buttonStyles({ variant: "secondary" })}
            >
              Review queue
            </Link>
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

      <nav
        aria-label="Quick filters"
        className="mb-5 flex flex-wrap gap-x-5 gap-y-1 border-b border-line"
      >
        <Tab
          label="All"
          value={counts?.total ?? "…"}
          active={!filtered}
          onClick={() => {
            setTyped({ search: "", industry: "" });
            setFilters(EMPTY_FILTERS);
          }}
        />
        <Tab
          label="Needs review"
          value={counts?.needs_review ?? "…"}
          active={filters.review === "needs_review"}
          onClick={() => set("review", "needs_review")}
        />
        <Tab
          label="Strong fit"
          value={counts?.strong ?? "…"}
          active={filters.icp_fit === "strong"}
          onClick={() => set("icp_fit", "strong")}
        />
        <Tab
          label="Trigger"
          value={counts?.trigger_yes ?? "…"}
          active={filters.trigger === "yes"}
          onClick={() => set("trigger", "yes")}
        />
        <Tab
          label="Not analyzed"
          value={counts?.not_assessed ?? "…"}
          active={filters.icp_fit === "unassessed"}
          onClick={() => set("icp_fit", "unassessed")}
        />
        <Tab
          label="Contacted"
          value={counts?.contacted ?? "…"}
          active={filters.outreach === "contacted"}
          onClick={() => set("outreach", "contacted")}
        />
        <Tab
          label="Replied"
          value={counts?.replied ?? "…"}
          active={filters.outreach === "replied"}
          onClick={() => set("outreach", "replied")}
        />
        <Tab
          label="Meetings"
          value={counts?.meetings ?? "…"}
          active={filters.outreach === "meeting_booked"}
          onClick={() => set("outreach", "meeting_booked")}
        />
        {(counts?.analyzing ?? 0) > 0 ? (
          <Tab label="Analyzing" value={counts?.analyzing ?? 0} />
        ) : null}
      </nav>

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

      <details className="mb-4 text-sm">
        <summary className="cursor-pointer text-fg-muted">More filters</summary>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <div className="w-28">
            <TextField
              label="Country"
              placeholder="SA"
              maxLength={2}
              value={filters.country}
              onChange={(e) => set("country", e.target.value.toUpperCase())}
            />
          </div>
          <div className="w-28">
            <TextField
              label="Size from"
              inputMode="numeric"
              value={filters.size_min}
              onChange={(e) =>
                set("size_min", e.target.value.replace(/\D/g, ""))
              }
            />
          </div>
          <div className="w-28">
            <TextField
              label="Size to"
              inputMode="numeric"
              value={filters.size_max}
              onChange={(e) =>
                set("size_max", e.target.value.replace(/\D/g, ""))
              }
            />
          </div>
          <div className="w-48">
            <TextField
              label="Buyer title"
              value={filters.buyer_title}
              onChange={(e) => set("buyer_title", e.target.value)}
            />
          </div>
          <label className="flex flex-col gap-1.5 text-sm font-medium">
            Trigger type
            <select
              value={filters.trigger_type}
              onChange={(e) => set("trigger_type", e.target.value)}
              className="rounded-md border border-line-strong bg-surface-raised px-3 py-1.5 text-sm font-normal"
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
      </details>

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
                <Th>Outreach</Th>
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
                  <Td className="text-fg-muted">
                    {row.outreach_status === "none"
                      ? "—"
                      : stageLabel(row.outreach_status)}
                  </Td>
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
