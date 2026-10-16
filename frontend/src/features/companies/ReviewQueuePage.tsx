"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button, buttonStyles } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { useOptionalSelection } from "@/features/selection/SelectionProvider";
import { errorMessage, formatDate } from "@/lib/format";

import { listReviewQueue } from "./accountsApi";
import {
  type DecisionChoice,
  getCard,
  recordDecision,
  type ResearchCard,
} from "./cardApi";

const KEYS: Record<string, DecisionChoice> = { a: "add", h: "hold", s: "skip" };
const HELP: ReadonlyArray<readonly [string, string]> = [
  ["A", "Add (approve)"],
  ["H", "Hold"],
  ["S", "Skip"],
  ["J or →", "Next account"],
  ["K or ←", "Previous account"],
  ["O", "Open the full card"],
  ["N", "Write a note"],
  ["?", "Show or hide this help"],
];

function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)
  );
}

/** `/companies/review`: clear the "needs review" accounts one after another, keyboard first. */
export function ReviewQueuePage() {
  const selection = useOptionalSelection();
  const campaign = selection?.campaign ?? null;
  const [queue, setQueue] = useState<string[] | null>(null);
  const [index, setIndex] = useState(0);
  const [reviewed, setReviewed] = useState(0);
  const [card, setCard] = useState<ResearchCard | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [help, setHelp] = useState(false);
  const cache = useRef(new Map<string, Promise<ResearchCard>>());
  const noteRef = useRef<HTMLTextAreaElement>(null);

  // The queue is the server's "needs review" list, so a reload simply picks up where you were.
  const campaignId = campaign?.id;
  useEffect(() => {
    if (!campaignId) return;
    const controller = new AbortController();
    cache.current.clear();
    listReviewQueue(campaignId, controller.signal)
      .then((ids) => {
        setQueue(ids);
        setIndex(0);
        setReviewed(0);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setError(errorMessage(cause));
      });
    return () => controller.abort();
  }, [campaignId]);

  const load = useCallback((id: string) => {
    let pending = cache.current.get(id);
    if (!pending) {
      pending = getCard(id);
      cache.current.set(id, pending);
      pending.catch(() => cache.current.delete(id));
    }
    return pending;
  }, []);

  const currentId = queue?.[index];
  const nextId = queue?.[index + 1];
  useEffect(() => {
    if (!currentId) {
      setCard(null);
      return;
    }
    let live = true;
    setCard(null);
    setNote("");
    load(currentId)
      .then((value) => live && setCard(value))
      .catch((cause) => live && setError(errorMessage(cause)));
    return () => {
      live = false;
    };
  }, [currentId, load]);
  useEffect(() => {
    if (nextId) void load(nextId).catch(() => undefined); // preload the next card
  }, [nextId, load]);

  const decide = useCallback(
    async (choice: DecisionChoice) => {
      if (!currentId || busy) return;
      setBusy(true);
      setError(null);
      try {
        await recordDecision(currentId, choice, note.trim());
        cache.current.delete(currentId);
        setQueue((current) => current?.filter((id) => id !== currentId) ?? []);
        setReviewed((count) => count + 1);
      } catch (cause) {
        setError(errorMessage(cause));
      } finally {
        setBusy(false);
      }
    },
    [currentId, busy, note],
  );

  const length = queue?.length ?? 0;
  const move = useCallback(
    (step: number) =>
      setIndex((now) =>
        Math.min(Math.max(now + step, 0), Math.max(length - 1, 0)),
      ),
    [length],
  );

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        typing(event.target)
      )
        return;
      const key = event.key.toLowerCase();
      const choice = KEYS[key];
      if (choice) {
        event.preventDefault();
        void decide(choice);
      } else if (key === "j" || key === "arrowright") move(1);
      else if (key === "k" || key === "arrowleft") move(-1);
      else if (key === "o" && currentId)
        window.location.assign(`/companies/${currentId}`);
      else if (key === "n") {
        event.preventDefault();
        noteRef.current?.focus();
      } else if (key === "?") setHelp((open) => !open);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [decide, move, currentId]);

  if (!campaign) {
    return (
      <>
        <PageHeader title="Review queue" />
        <EmptyState
          title="No campaign selected"
          description="Pick a client and a campaign in the header."
        />
      </>
    );
  }

  const total = reviewed + length;
  return (
    <>
      <PageHeader
        title="Review queue"
        description="Accounts waiting for your decision. Nothing is sent when you decide."
        actions={
          <>
            <Button variant="ghost" onClick={() => setHelp((open) => !open)}>
              Shortcuts (?)
            </Button>
            <Link
              href="/companies"
              className={buttonStyles({ variant: "secondary" })}
            >
              Back to companies
            </Link>
          </>
        }
      />
      {error ? (
        <Alert tone="danger" className="mb-4">
          {error}
        </Alert>
      ) : null}
      {help ? (
        <Card className="mb-4">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-4">
            {HELP.map(([keys, what]) => (
              <div key={keys} className="flex flex-col">
                <dt className="font-mono text-xs">{keys}</dt>
                <dd className="text-fg-muted">{what}</dd>
              </div>
            ))}
          </dl>
        </Card>
      ) : null}
      {queue === null ? (
        <Skeleton className="h-48 w-full" />
      ) : length === 0 ? (
        <EmptyState
          title={reviewed > 0 ? "Queue cleared" : "Nothing to review"}
          description={
            reviewed > 0
              ? `You decided on ${reviewed} ${reviewed === 1 ? "account" : "accounts"}.`
              : "Analyze companies first; accounts show up here once they have a recommendation."
          }
        />
      ) : (
        <>
          <div
            className="mb-3 flex items-center gap-3 text-sm text-fg-muted"
            aria-live="polite"
          >
            <span>
              {reviewed} reviewed · {length} left · account {index + 1} of{" "}
              {length}
            </span>
            <progress
              className="h-1.5 w-40"
              max={total}
              value={reviewed}
              aria-label="Review progress"
            />
          </div>
          {!card ? (
            <Skeleton className="h-64 w-full" />
          ) : (
            <Card>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 className="text-lg font-semibold">{card.company.name}</h2>
                  <p className="text-sm text-fg-muted">
                    {[card.company.domain, card.company.country]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                </div>
                <div className="flex gap-2">
                  {card.icp ? (
                    <Badge tone="info">Fit: {card.icp.fit}</Badge>
                  ) : null}
                  <Badge tone={card.trigger.triggered ? "success" : "neutral"}>
                    {card.trigger.triggered ? "Trigger" : "No trigger"}
                  </Badge>
                  {card.recommendation ? (
                    <Badge tone="neutral">
                      AI: {card.recommendation.status}
                    </Badge>
                  ) : null}
                </div>
              </div>
              {card.recommendation ? (
                <p className="mt-4 text-sm">
                  {card.recommendation.explanation}
                </p>
              ) : null}
              <div className="mt-4 grid gap-4 text-sm sm:grid-cols-2">
                <ul className="list-disc pl-5">
                  {(card.icp?.reasons ?? []).map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
                <ul className="list-disc pl-5 text-fg-muted">
                  {(card.icp?.concerns ?? []).map((concern) => (
                    <li key={concern}>{concern}</li>
                  ))}
                </ul>
              </div>
              {card.trigger.signals.length > 0 ? (
                <ul className="mt-4 flex flex-col gap-1 text-sm">
                  {card.trigger.signals.map((signal) => (
                    <li key={signal.id}>
                      <span className="font-medium">{signal.type_label}</span> (
                      {formatDate(signal.event_date)}):{" "}
                      <span className="text-fg-muted">{signal.evidence}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
              <label className="mt-4 block text-sm">
                <span className="text-fg-muted">Note (optional, press N)</span>
                <textarea
                  ref={noteRef}
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                  maxLength={2000}
                  rows={2}
                  className="mt-1 w-full rounded-md border border-line bg-surface p-2"
                />
              </label>
              <div className="mt-4 flex flex-wrap gap-2">
                <Button disabled={busy} onClick={() => decide("add")}>
                  Add (A)
                </Button>
                <Button
                  variant="secondary"
                  disabled={busy}
                  onClick={() => decide("hold")}
                >
                  Hold (H)
                </Button>
                <Button
                  variant="secondary"
                  disabled={busy}
                  onClick={() => decide("skip")}
                >
                  Skip (S)
                </Button>
                <Button variant="ghost" onClick={() => move(-1)}>
                  Previous (K)
                </Button>
                <Button variant="ghost" onClick={() => move(1)}>
                  Next (J)
                </Button>
                <Link
                  href={`/companies/${card.company.id}`}
                  className={buttonStyles({ variant: "ghost" })}
                >
                  Open card (O)
                </Link>
              </div>
            </Card>
          )}
        </>
      )}
    </>
  );
}
