"use client";

import { useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { errorMessage } from "@/lib/format";

import { applyRanking, type BuyerRanking, getRanking } from "./rankingApi";

const ROLE: Record<string, string> = {
  primary: "Suggested primary",
  secondary: "Suggested secondary",
};

/** "Who first?": the suggested order of the people, with the reasons, applied only on request. */
export function RankingPanel({
  companyId,
  onApplied,
}: {
  companyId: string;
  onApplied: () => void;
}) {
  const [ranking, setRanking] = useState<BuyerRanking | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(action: () => Promise<BuyerRanking>, applied: boolean) {
    setBusy(true);
    setError(null);
    try {
      const result = await action();
      setRanking(applied ? null : result);
      if (applied) onApplied();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  if (!ranking) {
    return (
      <>
        <Button
          variant="secondary"
          size="sm"
          disabled={busy}
          onClick={() => run(() => getRanking(companyId), false)}
        >
          {busy ? "Thinking…" : "Who should I contact first?"}
        </Button>
        {error ? (
          <Alert tone="danger" className="mt-2">
            {error}
          </Alert>
        ) : null}
      </>
    );
  }
  return (
    <div className="rounded-md border border-line p-3 text-sm">
      <p className="mb-2 font-medium">Suggested order</p>
      <p className="mb-3 text-xs text-fg-muted">{ranking.note}</p>
      <ol className="flex flex-col gap-3">
        {ranking.people.map((person) => (
          <li key={person.contact_id}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">
                {person.rank}. {person.name}
              </span>
              <span className="text-fg-muted">
                {person.title || "No title"}
              </span>
              {ROLE[person.suggested_role] ? (
                <Badge tone="info">{ROLE[person.suggested_role]}</Badge>
              ) : null}
            </div>
            <ul className="mt-0.5 list-disc pl-5 text-xs text-fg-muted">
              {person.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
      {error ? (
        <Alert tone="danger" className="mt-2">
          {error}
        </Alert>
      ) : null}
      <div className="mt-3 flex gap-2">
        <Button
          size="sm"
          disabled={busy}
          onClick={() => run(() => applyRanking(companyId), true)}
        >
          Use this order
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setRanking(null)}>
          Close
        </Button>
      </div>
    </div>
  );
}
