"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button, buttonStyles } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Table, Td, Th } from "@/components/ui/Table";
import { useOptionalSelection } from "@/features/selection/SelectionProvider";
import { errorMessage, formatDateTime } from "@/lib/format";
import { useApiQuery } from "@/lib/hooks/useApiQuery";

import {
  cancelImport,
  type CsvPreview,
  downloadProblems,
  downloadTemplate,
  getImportBatch,
  type ImportBatch,
  listImports,
  type Mapping,
  previewCsv,
  startCsvImport,
} from "./importApi";

const FIELD_LABEL: Record<string, string> = {
  name: "Company name",
  website: "Website",
  profile_url: "Profile link",
  country: "Country",
};
const DONE = new Set(["completed", "partial", "failed", "cancelled"]);
const POLL_MS = 2000;

/** `/companies/import`: bring in a list of companies from a CSV file (Brief section 4B). */
export function ImportPage() {
  const campaign = useOptionalSelection()?.campaign ?? null;
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<CsvPreview | null>(null);
  const [mapping, setMapping] = useState<Mapping>({});
  const [checking, setChecking] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [batch, setBatch] = useState<ImportBatch | null>(null);
  const input = useRef<HTMLInputElement>(null);

  // While the import runs, ask for its progress every couple of seconds.
  const batchId = batch?.id;
  const finished = batch ? DONE.has(batch.status) : true;
  useEffect(() => {
    if (!batchId || finished) return;
    const timer = setInterval(() => {
      getImportBatch(batchId).then(setBatch, () => undefined);
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [batchId, finished]);

  async function check(next: File, nextMapping?: Mapping) {
    if (!campaign) return;
    setChecking(true);
    setError(null);
    try {
      const result = await previewCsv(campaign.id, next, nextMapping);
      setPreview(result);
      setMapping(result.mapping);
    } catch (cause) {
      setPreview(null);
      setError(errorMessage(cause));
    } finally {
      setChecking(false);
    }
  }

  function choose(next: File | null) {
    setFile(next);
    setPreview(null);
    setBatch(null);
    if (next) void check(next);
  }

  async function start() {
    if (!campaign || !file) return;
    setStarting(true);
    setError(null);
    try {
      setBatch(await startCsvImport(campaign.id, file, mapping));
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setStarting(false);
    }
  }

  const header = (
    <PageHeader
      title="Import companies"
      description="Upload a CSV file with the companies you want to qualify."
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

  if (batch) {
    return (
      <>
        {header}
        <Progress batch={batch} />
        <RecentImports campaignId={campaign.id} refreshKey={batch.status} />
      </>
    );
  }

  const seen = preview?.rows.filter((r) => r.errors.length > 0).length ?? 0;
  const problems = Math.max(preview?.error_rows ?? 0, seen);
  const importable = (preview?.total_rows ?? 0) - problems;
  const noName = preview !== null && !Object.values(mapping).includes("name");

  return (
    <>
      {header}
      <Card title="1. Choose your file">
        <p className="mb-3 text-sm text-fg-muted">
          One row per company. A name is required; a website helps the research
          a lot. Up to {preview?.limits.max_rows ?? 5000} rows.{" "}
          <button
            type="button"
            className="text-accent underline-offset-2 hover:underline"
            onClick={() =>
              downloadTemplate().catch((c) => setError(errorMessage(c)))
            }
          >
            Download a template
          </button>
        </p>
        <div
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            choose(e.dataTransfer.files[0] ?? null);
          }}
          className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-line-strong px-6 py-8 text-center"
        >
          <p className="text-sm">
            {file ? (
              <>
                <span className="font-medium">{file.name}</span>{" "}
                <span className="text-fg-muted">
                  ({Math.max(1, Math.round(file.size / 1024))} KB)
                </span>
              </>
            ) : (
              "Drop a CSV file here"
            )}
          </p>
          <input
            ref={input}
            type="file"
            accept=".csv,text/csv"
            className="sr-only"
            aria-label="CSV file"
            onChange={(e) => choose(e.target.files?.[0] ?? null)}
          />
          <Button variant="secondary" onClick={() => input.current?.click()}>
            {file ? "Choose a different file" : "Choose a file"}
          </Button>
        </div>
        {checking ? (
          <p role="status" className="mt-3 text-sm text-fg-muted">
            Reading your file…
          </p>
        ) : null}
        {error ? (
          <Alert tone="danger" className="mt-3">
            {error}
          </Alert>
        ) : null}
      </Card>

      <RecentImports campaignId={campaign.id} refreshKey="idle" />

      {preview ? (
        <>
          <Card title="2. Check the columns" className="mt-5">
            <p className="mb-3 text-sm text-fg-muted">
              We matched your columns to company fields. Change any that look
              wrong.
            </p>
            <Table caption="Column matching">
              <thead>
                <tr>
                  <Th>Column in your file</Th>
                  <Th>Becomes</Th>
                  <Th>Example</Th>
                </tr>
              </thead>
              <tbody>
                {preview.headers.map((header) => (
                  <tr key={header}>
                    <Td className="font-medium">{header}</Td>
                    <Td>
                      <select
                        aria-label={`What ${header} is`}
                        value={mapping[header] ?? ""}
                        onChange={(e) => {
                          const next = { ...mapping };
                          if (e.target.value) next[header] = e.target.value;
                          else delete next[header];
                          setMapping(next);
                          if (file) void check(file, next);
                        }}
                        className="rounded-md border border-line-strong bg-surface-raised px-3 py-1.5 text-sm"
                      >
                        <option value="">Don&apos;t import</option>
                        {preview.company_fields.map((field) => (
                          <option key={field} value={field}>
                            {FIELD_LABEL[field] ?? field}
                          </option>
                        ))}
                      </select>
                      {preview.fuzzy_headers.includes(header) ? (
                        <span className="ml-2 text-xs text-warning-fg">
                          Best guess, please check
                        </span>
                      ) : null}
                    </Td>
                    <Td className="text-fg-muted">
                      {preview.rows[0]?.raw[header] ?? ""}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            {noName ? (
              <Alert tone="warning" className="mt-3">
                Choose which column holds the company name.
              </Alert>
            ) : null}
          </Card>

          <Card title="3. Review and import" className="mt-5">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Badge tone="info">{preview.total_rows} companies found</Badge>
              {problems > 0 ? (
                <Badge tone="warning">{problems} with problems</Badge>
              ) : (
                <Badge tone="success">No problems found</Badge>
              )}
              {preview.blank_rows > 0 ? (
                <span className="text-fg-muted">
                  {preview.blank_rows} blank rows are ignored
                </span>
              ) : null}
            </div>
            {preview.warnings.map((w) => (
              <Alert key={w.code} tone="info" className="mt-3">
                {w.message}
              </Alert>
            ))}
            {preview.rows.some((r) => r.errors.length > 0) ? (
              <div className="mt-4">
                <p className="mb-2 text-sm font-medium">
                  Rows that will be skipped (first few)
                </p>
                <ul className="flex flex-col gap-1.5 text-sm">
                  {preview.rows
                    .filter((r) => r.errors.length > 0)
                    .slice(0, 8)
                    .map((r) => (
                      <li key={r.row_number} className="text-fg-muted">
                        Row {r.row_number}:{" "}
                        {r.errors.map((e) => e.message).join(" ")}
                      </li>
                    ))}
                </ul>
              </div>
            ) : null}
            {preview.active_import ? (
              <Alert tone="warning" className="mt-3">
                This file is already being imported into this campaign. Wait for
                it to finish.
              </Alert>
            ) : null}
            <div className="mt-4 flex items-center gap-3">
              <Button
                onClick={start}
                disabled={
                  starting ||
                  checking ||
                  noName ||
                  importable <= 0 ||
                  preview.active_import !== null
                }
              >
                {starting
                  ? "Starting…"
                  : `Import ${Math.max(importable, 0)} companies`}
              </Button>
              <span className="text-xs text-fg-muted">
                Companies already in the campaign are recognised and not added
                twice.
              </span>
            </div>
          </Card>
        </>
      ) : null}
    </>
  );
}

function Progress({ batch }: { batch: ImportBatch }) {
  const done = DONE.has(batch.status);
  const [error, setError] = useState<string | null>(null);
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
        {batch.processed_count} of {batch.total_count} rows processed
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
      {batch.failed_count > 0 && batch.status !== "failed" ? (
        <p className="mt-3 text-sm text-fg-muted">
          Rows that could not be added are usually missing a company name or
          have a website that is not valid. Download them to see exactly why.
        </p>
      ) : batch.error_summary ? (
        <Alert tone="warning" className="mt-3">
          {batch.error_summary}
        </Alert>
      ) : null}
      {error ? (
        <Alert tone="danger" className="mt-3">
          {error}
        </Alert>
      ) : null}
      {done ? (
        <div className="mt-5 flex flex-wrap gap-2">
          <Link href="/companies" className={buttonStyles()}>
            See the companies
          </Link>
          {batch.failed_count > 0 ? (
            <Button
              variant="secondary"
              onClick={() =>
                downloadProblems(batch.id).catch((c) =>
                  setError(errorMessage(c)),
                )
              }
            >
              Download the rows with problems
            </Button>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}

/** The campaign's recent imports, with a way to stop one that is still running. */
function RecentImports({
  campaignId,
  refreshKey,
}: {
  campaignId: string;
  refreshKey: string;
}) {
  const { data, reload } = useApiQuery(
    `imports:${campaignId}:${refreshKey}`,
    (signal) => listImports(campaignId, signal),
  );
  const [error, setError] = useState<string | null>(null);
  const rows = Array.isArray(data) ? data.slice(0, 8) : [];
  if (rows.length === 0) return null;
  return (
    <Card title="Recent imports" className="mt-5">
      <ul className="flex flex-col gap-3 text-sm">
        {rows.map((row) => {
          const running =
            row.status === "pending" || row.status === "processing";
          return (
            <li
              key={row.id}
              className="flex flex-wrap items-center justify-between gap-2"
            >
              <span>
                <span className="font-medium">
                  {row.original_filename || "Manual entry"}
                </span>{" "}
                <span className="text-fg-muted">
                  {formatDateTime(row.created_at)} · {row.created_count} added
                  {row.failed_count ? `, ${row.failed_count} failed` : ""}
                </span>
              </span>
              <span className="flex items-center gap-2">
                <Badge
                  tone={
                    row.status === "completed"
                      ? "success"
                      : row.status === "failed" || row.status === "partial"
                        ? "warning"
                        : "neutral"
                  }
                >
                  {row.status}
                </Badge>
                {running ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() =>
                      cancelImport(row.id).then(reload, (c) =>
                        setError(errorMessage(c)),
                      )
                    }
                  >
                    Cancel
                  </Button>
                ) : null}
              </span>
            </li>
          );
        })}
      </ul>
      {error ? (
        <Alert tone="danger" className="mt-3">
          {error}
        </Alert>
      ) : null}
    </Card>
  );
}
