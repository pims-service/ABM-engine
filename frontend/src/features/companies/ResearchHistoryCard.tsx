"use client";

import { Card } from "@/components/ui/Card";
import { errorMessage, formatDateTime } from "@/lib/format";
import { useApiQuery } from "@/lib/hooks/useApiQuery";

import { getResearchHistory } from "./historyApi";

/** What the research found each time, and what changed since the run before (Brief section 19). */
export function ResearchHistoryCard({
  companyId,
  latestId,
}: {
  companyId: string;
  /** The current snapshot: a new one reloads the list. */
  latestId: string | null;
}) {
  const history = useApiQuery(
    `research-history:${companyId}:${latestId}`,
    (signal) => getResearchHistory(companyId, signal),
  );
  const rows = history.data ?? [];
  if (!history.error && rows.length < 2) return null; // nothing to compare yet

  return (
    <Card title="Research history">
      {history.error ? (
        <p className="text-sm text-fg-muted">{errorMessage(history.error)}</p>
      ) : (
        <ol className="flex flex-col gap-4 text-sm">
          {rows.map((row) => (
            <li key={row.id}>
              <p className="font-medium">
                {formatDateTime(row.researched_at)}{" "}
                <span className="font-normal text-fg-muted">
                  · {row.source}
                </span>
              </p>
              {row.first ? (
                <p className="text-fg-muted">First research.</p>
              ) : row.changes.length === 0 ? (
                <p className="text-fg-muted">Nothing changed.</p>
              ) : (
                <ul className="mt-1 flex flex-col gap-0.5">
                  {row.changes.map((change) => (
                    <li key={change.field}>
                      {change.label}:{" "}
                      <span className="text-fg-muted">
                        {change.old ?? "not found"}
                      </span>{" "}
                      → {change.new ?? "not found"}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}
