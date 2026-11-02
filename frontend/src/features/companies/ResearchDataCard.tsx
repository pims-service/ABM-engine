"use client";

import { useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { errorMessage, formatDate, formatDateTime } from "@/lib/format";
import { useApiQuery } from "@/lib/hooks/useApiQuery";

import {
  type EvidenceRow,
  getEvidence,
  getMergedProfile,
  getResearchRuns,
  type ProfileField,
  type ResearchRun,
  startResearch,
} from "./researchApi";

const STEP_LABEL: Record<string, string> = {
  profile: "Company profile",
  signals: "Signals and hiring",
  news: "News",
};

const STATUS_TONE: Record<string, BadgeTone> = {
  succeeded: "success",
  failed: "danger",
  partial: "warning",
  running: "info",
  queued: "neutral",
};

const SOURCE_TYPE: Record<string, string> = {
  provider: "Data provider",
  website: "Website",
  news: "News",
  manual: "Entered by hand",
};

/** Counts the step reported, for example `{"skipped_reason": "..."}` or `missing_fields`. */
function stepNote(result: unknown): string | null {
  if (!result || typeof result !== "object") return null;
  const data = result as Record<string, unknown>;
  if (typeof data.skipped_reason === "string" && data.skipped_reason) {
    return data.skipped_reason;
  }
  if (Array.isArray(data.missing_fields) && data.missing_fields.length > 0) {
    return `${data.missing_fields.length} fields not returned by the provider.`;
  }
  return null;
}

function RunSummary({ run }: { run: ResearchRun }) {
  return (
    <div className="rounded-md border border-line p-3 text-sm">
      <p className="mb-2 flex flex-wrap items-center gap-2">
        <span className="font-medium">Latest run</span>
        <Badge tone={STATUS_TONE[run.status] ?? "neutral"}>{run.status}</Badge>
        <span className="text-fg-muted">{formatDateTime(run.created_at)}</span>
      </p>
      <ul className="flex flex-col gap-1">
        {run.steps.map((step) => {
          const note = step.error || stepNote(step.result);
          return (
            <li key={step.step}>
              {STEP_LABEL[step.step] ?? step.step}:{" "}
              <span className="text-fg-muted">{step.status}</span>
              {note ? <span className="text-fg-muted"> · {note}</span> : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function EvidenceList({ rows }: { rows: EvidenceRow[] }) {
  if (rows.length === 0) {
    return <p className="text-sm text-fg-muted">No sources recorded yet.</p>;
  }
  return (
    <ul className="flex flex-col gap-3 text-sm">
      {rows.map((row) => (
        <li key={row.id}>
          <p className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{row.name}</span>
            <Badge tone="neutral">{SOURCE_TYPE[row.type] ?? row.type}</Badge>
            <span className="text-fg-muted">
              retrieved {formatDate(row.retrieved_at)} ({row.age_days}{" "}
              {row.age_days === 1 ? "day" : "days"} ago)
            </span>
          </p>
          {row.url ? (
            <a
              href={row.url}
              target="_blank"
              rel="noreferrer"
              className="break-all text-xs text-accent underline-offset-2 hover:underline"
            >
              {row.url}
            </a>
          ) : null}
          <p className="text-xs text-fg-muted">
            Backs {row.backs.length} {row.backs.length === 1 ? "item" : "items"}
            {row.evidence_date
              ? ` · the source states ${formatDate(row.evidence_date)}`
              : ""}
          </p>
        </li>
      ))}
    </ul>
  );
}

function MergedProfile({ fields }: { fields: ProfileField[] }) {
  if (fields.length === 0) {
    return <p className="text-sm text-fg-muted">Nothing found yet.</p>;
  }
  return (
    <dl className="flex flex-col gap-2 text-sm">
      {fields.map((field) => (
        <div key={field.field}>
          <dt className="text-fg-muted">{field.name}</dt>
          <dd>
            {field.value}{" "}
            <span className="text-xs text-fg-muted">({field.origin.name})</span>
            {field.alternatives.length > 0 ? (
              <p className="text-xs text-fg-muted">
                Also found:{" "}
                {field.alternatives
                  .map((alt) => `${alt.value} (${alt.origin.name})`)
                  .join(", ")}
              </p>
            ) : null}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * Data sources and collection: start a provider collection run, see what it did, and read the
 * evidence and the merged profile with the source of every value (Brief sections 5 and 19).
 */
export function ResearchDataCard({
  companyId,
  onCollected,
}: {
  companyId: string;
  /** Called after a run was queued, so the page can reload what the run may have changed. */
  onCollected?: () => void;
}) {
  const [version, setVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const key = `${companyId}:${version}`;
  const runs = useApiQuery(`research-runs:${key}`, (signal) =>
    getResearchRuns(companyId, signal),
  );
  const evidence = useApiQuery(`evidence:${key}`, (signal) =>
    getEvidence(companyId, signal),
  );
  const profile = useApiQuery(`merged-profile:${key}`, (signal) =>
    getMergedProfile(companyId, signal),
  );

  async function collect() {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const result = await startResearch(companyId);
      setNote(
        result.already_queued
          ? "A collection is already in progress."
          : "Collection started. Refresh in a moment to see the result.",
      );
      setVersion((v) => v + 1);
      onCollected?.();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  const latest = runs.data?.[0];
  const loadError = runs.error ?? evidence.error ?? profile.error;

  return (
    <Card title="Data sources and collection">
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            disabled={busy}
            onClick={collect}
          >
            {busy ? "Starting…" : "Collect provider data"}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setVersion((v) => v + 1)}
          >
            Refresh
          </Button>
        </div>
        <p className="text-xs text-fg-muted">
          Asks the connected company-data provider for the profile, headcounts,
          funding, job openings and news. It does not read the website or use
          the AI model; that is Analyze Company.
        </p>
        {note ? <Alert tone="info">{note}</Alert> : null}
        {error ? <Alert tone="danger">{error}</Alert> : null}
        {loadError ? (
          <p className="text-sm text-fg-muted">{errorMessage(loadError)}</p>
        ) : null}
        {latest ? <RunSummary run={latest} /> : null}

        <section>
          <h3 className="mb-2 text-sm font-medium">Sources</h3>
          <EvidenceList rows={evidence.data ?? []} />
        </section>
        <section>
          <h3 className="mb-2 text-sm font-medium">
            Profile and where it comes from
          </h3>
          <MergedProfile fields={profile.data ?? []} />
        </section>
      </div>
    </Card>
  );
}
