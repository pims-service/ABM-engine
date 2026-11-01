"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button, buttonStyles } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Select } from "@/components/ui/Select";
import { Table, Td, Th } from "@/components/ui/Table";
import { TextField } from "@/components/ui/TextField";
import { useOptionalSelection } from "@/features/selection/SelectionProvider";
import { errorMessage } from "@/lib/format";
import { useApiQuery } from "@/lib/hooks/useApiQuery";

import {
  getProviderImport,
  importSelected,
  listProviders,
  type PreviewItem,
  type ProviderImportBatch,
  type ProviderSearch,
  type SearchFilters,
  searchProvider,
} from "./providerImportApi";

const POLL_MS = 2000;
const DONE = new Set(["completed", "partial", "failed", "cancelled"]);

/** What a result's duplicate check says, in words, and how loudly. */
const STATUS: Record<
  string,
  { label: string; tone: "success" | "warning" | "neutral" }
> = {
  new: { label: "New", tone: "success" },
  duplicate: { label: "Already in this campaign", tone: "neutral" },
  possible_duplicate: { label: "Looks like one you have", tone: "warning" },
  already_imported: { label: "Imported before", tone: "neutral" },
  invalid: { label: "Cannot be imported", tone: "warning" },
};

function split(text: string): string[] {
  return text
    .split(/[,;\n]/)
    .map((part) => part.trim())
    .filter(Boolean);
}

function number(text: string): number | null {
  const value = Number(text.trim());
  return text.trim() === "" || !Number.isFinite(value) ? null : value;
}

/** Companies that are new are ticked for you; the ones already there are not. */
function defaultSelection(items: PreviewItem[], offset = 0): string[] {
  return items.flatMap((item, index) =>
    item.importable && item.dedupe.status === "new"
      ? [key(item, offset + index)]
      : [],
  );
}

function key(item: PreviewItem, index: number): string {
  return item.provider_key ?? `row-${index}`;
}

/** `/companies/find`: search a data provider with the campaign's filters and import what you pick. */
export function FindCompaniesPage() {
  const campaign = useOptionalSelection()?.campaign ?? null;
  const providers = useApiQuery("providers", (signal) => listProviders(signal));
  const searchable = (providers.data ?? []).filter((p) => p.supports_search);

  const [providerName, setProviderName] = useState("");
  const [query, setQuery] = useState("");
  const [countries, setCountries] = useState("");
  const [industries, setIndustries] = useState("");
  const [employeeMin, setEmployeeMin] = useState("");
  const [employeeMax, setEmployeeMax] = useState("");
  const [result, setResult] = useState<ProviderSearch | null>(null);
  const [items, setItems] = useState<PreviewItem[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [searching, setSearching] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [batch, setBatch] = useState<ProviderImportBatch | null>(null);
  const started = useRef(false);

  const provider =
    searchable.find((p) => p.name === providerName) ?? searchable[0] ?? null;

  function filters(): SearchFilters {
    return {
      countries: split(countries).map((c) => c.toUpperCase()),
      industries: split(industries),
      employee_min: number(employeeMin),
      employee_max: number(employeeMax),
    };
  }

  /** Fill the boxes with what the search really used (the campaign's own filters at first). */
  function showSearchUsed(found: ProviderSearch) {
    setCountries(found.search.countries.join(", "));
    setIndustries(found.search.industries.join(", "));
    setEmployeeMin(found.search.employee_min?.toString() ?? "");
    setEmployeeMax(found.search.employee_max?.toString() ?? "");
  }

  async function run(options: { fresh: boolean; prefill?: boolean }) {
    if (!campaign || !provider) return;
    setSearching(true);
    setError(null);
    try {
      const found = await searchProvider(campaign.id, {
        provider: provider.name,
        query,
        // The first search has no overrides: it uses the campaign's own filters.
        filters: options.prefill ? undefined : filters(),
        cursor: options.fresh ? null : result?.next_cursor,
      });
      if (options.prefill) showSearchUsed(found);
      if (options.fresh) {
        setItems(found.items);
        setSelected(new Set(defaultSelection(found.items)));
      } else {
        setSelected(
          (current) =>
            new Set([
              ...current,
              ...defaultSelection(found.items, items.length),
            ]),
        );
        setItems((current) => [...current, ...found.items]);
      }
      setResult(found);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setSearching(false);
    }
  }

  // Search once as soon as there is a campaign and a provider, so the filters show what the
  // campaign already says.
  useEffect(() => {
    if (started.current || !campaign || !provider) return;
    started.current = true;
    void run({ fresh: true, prefill: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campaign?.id, provider?.name]);

  // While a large import runs in the background, look again every couple of seconds.
  const batchId = batch?.id;
  const finished = batch ? DONE.has(batch.status) : true;
  useEffect(() => {
    if (!batchId || finished) return;
    const timer = setInterval(() => {
      getProviderImport(batchId).then(setBatch, () => undefined);
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [batchId, finished]);

  function toggle(rowKey: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (!next.delete(rowKey)) next.add(rowKey);
      return next;
    });
  }

  async function submit() {
    if (!campaign || !provider || !result) return;
    const chosen = items.filter((item, index) =>
      selected.has(key(item, index)),
    );
    setImporting(true);
    setError(null);
    try {
      setBatch(
        await importSelected(campaign.id, {
          provider: provider.name,
          companies: chosen.map((item) => item.company),
          search: { query, filters: filters() },
          retrievedAt: result.retrieved_at,
        }),
      );
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setImporting(false);
    }
  }

  const header = (
    <PageHeader
      title="Find companies"
      description="Search a data provider with this campaign's filters and import the ones you tick."
      actions={
        <Link
          href="/companies"
          className={buttonStyles({ variant: "secondary" })}
        >
          Back to companies
        </Link>
      }
    />
  );

  if (!campaign) {
    return (
      <>
        {header}
        <EmptyState
          title="Pick a campaign first"
          description="Choose a client and a campaign in the header. Imported companies join that campaign."
        />
      </>
    );
  }

  if (providers.loading && !providers.data) {
    return (
      <>
        {header}
        <p role="status" className="text-sm text-fg-muted">
          Loading providers…
        </p>
      </>
    );
  }

  if (!provider) {
    return (
      <>
        {header}
        <EmptyState
          title="No data provider is connected"
          description="Connect a company data provider first, then come back to search it."
          action={
            <Link href="/settings/integrations" className={buttonStyles()}>
              Open integrations
            </Link>
          }
        />
      </>
    );
  }

  if (batch) {
    return (
      <>
        {header}
        <Summary
          batch={batch}
          onAgain={() => {
            setBatch(null);
            void run({ fresh: true });
          }}
        />
      </>
    );
  }

  const max = provider.max_import_selection;
  const tooMany = selected.size > max;

  return (
    <>
      {header}
      <Card className="mb-5">
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void run({ fresh: true });
          }}
        >
          {searchable.length > 1 ? (
            <Select
              label="Provider"
              value={provider.name}
              onChange={(event) => setProviderName(event.target.value)}
            >
              {searchable.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.name}
                </option>
              ))}
            </Select>
          ) : null}
          <div className="min-w-48 flex-1">
            <TextField
              label="Search"
              placeholder="Name or keyword"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <div className="w-40">
            <TextField
              label="Countries"
              placeholder="SA, AE"
              value={countries}
              onChange={(event) => setCountries(event.target.value)}
            />
          </div>
          <div className="min-w-40 flex-1">
            <TextField
              label="Industries"
              value={industries}
              onChange={(event) => setIndustries(event.target.value)}
            />
          </div>
          <div className="w-28">
            <TextField
              label="Employees from"
              inputMode="numeric"
              value={employeeMin}
              onChange={(event) =>
                setEmployeeMin(event.target.value.replace(/\D/g, ""))
              }
            />
          </div>
          <div className="w-28">
            <TextField
              label="Employees to"
              inputMode="numeric"
              value={employeeMax}
              onChange={(event) =>
                setEmployeeMax(event.target.value.replace(/\D/g, ""))
              }
            />
          </div>
          <Button type="submit" disabled={searching}>
            {searching ? "Searching…" : "Search"}
          </Button>
        </form>
      </Card>

      {error ? (
        <Alert tone="danger" className="mb-4">
          {error}
        </Alert>
      ) : null}
      {result?.warnings.map((warning) => (
        <Alert key={warning} tone="info" className="mb-4">
          {warning}
        </Alert>
      ))}

      {searching && items.length === 0 ? (
        <p role="status" className="text-sm text-fg-muted">
          Searching…
        </p>
      ) : result && items.length === 0 ? (
        <EmptyState
          title="No companies found"
          description="Widen the filters or change the search words, then search again."
        />
      ) : items.length > 0 ? (
        <>
          <Table caption="Companies found">
            <thead>
              <tr>
                <Th className="w-10">
                  <span className="sr-only">Select</span>
                </Th>
                <Th>Company</Th>
                <Th>Country</Th>
                <Th>Industry</Th>
                <Th>Employees</Th>
                <Th>In this campaign</Th>
              </tr>
            </thead>
            <tbody>
              {items.map((item, index) => {
                const rowKey = key(item, index);
                const status = STATUS[item.dedupe.status] ?? {
                  label: item.dedupe.status,
                  tone: "neutral" as const,
                };
                return (
                  <tr key={rowKey}>
                    <Td>
                      <input
                        type="checkbox"
                        aria-label={`Select ${item.company.name}`}
                        className="h-4 w-4 accent-accent"
                        checked={selected.has(rowKey)}
                        disabled={!item.importable}
                        onChange={() => toggle(rowKey)}
                      />
                    </Td>
                    <Td>
                      <span className="font-medium">{item.company.name}</span>
                      <span className="block text-xs text-fg-muted">
                        {item.company.website ||
                          item.company.domain_hint ||
                          "—"}
                      </span>
                    </Td>
                    <Td className="text-fg-muted">
                      {item.company.country || "—"}
                    </Td>
                    <Td className="text-fg-muted">
                      {item.company.industry || "—"}
                    </Td>
                    <Td className="text-fg-muted">
                      {item.company.employee_count ??
                        item.company.employee_range ??
                        "—"}
                    </Td>
                    <Td>
                      <Badge tone={status.tone}>{status.label}</Badge>
                      {item.dedupe.company ? (
                        <span className="mt-1 block text-xs text-fg-muted">
                          {item.dedupe.company.name}
                        </span>
                      ) : null}
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-fg-muted" aria-live="polite">
              {selected.size} selected
              {result?.total_estimate
                ? ` · about ${result.total_estimate} match`
                : ""}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              {result?.next_cursor ? (
                <Button
                  variant="secondary"
                  disabled={searching}
                  onClick={() => void run({ fresh: false })}
                >
                  {searching ? "Loading…" : "Show more"}
                </Button>
              ) : null}
              <Button
                disabled={selected.size === 0 || tooMany || importing}
                onClick={() => void submit()}
              >
                {importing
                  ? "Importing…"
                  : `Import ${selected.size} ${selected.size === 1 ? "company" : "companies"}`}
              </Button>
            </div>
          </div>
          {tooMany ? (
            <Alert tone="warning" className="mt-3">
              You can import at most {max} companies at a time. Untick some.
            </Alert>
          ) : null}
        </>
      ) : null}
    </>
  );
}

/** What the import did, with a way back to the companies. */
function Summary({
  batch,
  onAgain,
}: {
  batch: ProviderImportBatch;
  onAgain: () => void;
}) {
  const done = DONE.has(batch.status);
  return (
    <Card title={done ? "Import finished" : "Importing…"}>
      <div
        className="h-2 w-full overflow-hidden rounded-full bg-surface-muted"
        role="progressbar"
        aria-valuenow={batch.progress_percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className="h-full bg-accent transition-all"
          style={{ width: `${batch.progress_percent}%` }}
        />
      </div>
      <p className="mt-2 text-sm text-fg-muted">
        {batch.processed_count} of {batch.total_count} companies processed
      </p>
      <div className="mt-4 flex flex-wrap gap-2 text-sm">
        <Badge tone="success">{batch.created_count} added</Badge>
        {batch.duplicate_count > 0 ? (
          <Badge>{batch.duplicate_count} already there</Badge>
        ) : null}
        {batch.restored_count > 0 ? (
          <Badge>{batch.restored_count} restored</Badge>
        ) : null}
        {batch.failed_count > 0 ? (
          <Badge tone="warning">{batch.failed_count} could not be added</Badge>
        ) : null}
      </div>
      {batch.error_summary ? (
        <Alert tone="warning" className="mt-3">
          {batch.error_summary}
        </Alert>
      ) : null}
      {done ? (
        <div className="mt-5 flex flex-wrap gap-2">
          <Link href="/companies" className={buttonStyles()}>
            See the companies
          </Link>
          <Button variant="secondary" onClick={onAgain}>
            Find more
          </Button>
        </div>
      ) : null}
    </Card>
  );
}
