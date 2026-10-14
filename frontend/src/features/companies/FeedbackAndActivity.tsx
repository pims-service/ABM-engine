"use client";

import { useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Textarea } from "@/components/ui/Textarea";
import { errorMessage, formatDateTime } from "@/lib/format";

import {
  type ActivityItem,
  FEEDBACK_KINDS,
  type FeedbackKind,
  sendFeedback,
} from "./cardApi";

/** The company's timeline (Brief section 14): research, recommendations, decisions, feedback. */
export function ActivityCard({ items }: { items: ActivityItem[] }) {
  return (
    <Card title="Activity">
      {items.length === 0 ? (
        <p className="text-sm text-fg-muted">Nothing has happened yet.</p>
      ) : (
        <ol className="flex flex-col gap-3 text-sm">
          {items.map((item) => (
            <li key={item.id} className="flex flex-col">
              <span>{item.summary}</span>
              {item.detail ? (
                <span className="text-xs text-fg-muted">{item.detail}</span>
              ) : null}
              <span className="text-xs text-fg-muted">
                {formatDateTime(item.created_at)}
                {item.actor ? ` · ${item.actor}` : " · system"}
              </span>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

/** Flag something the system got wrong (Brief section 16). It is stored, nothing else changes. */
export function FeedbackCard({
  companyId,
  onSent,
}: {
  companyId: string;
  onSent: () => void;
}) {
  const [kind, setKind] = useState<FeedbackKind>("wrong_buyer");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function submit() {
    setSaving(true);
    setError(null);
    setDone(false);
    try {
      await sendFeedback(companyId, { kind, note: note.trim() });
      setNote("");
      setDone(true);
      onSent();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card title="Flag a problem">
      <p className="mb-3 text-sm text-fg-muted">
        Tell the system what it got wrong. It is kept to improve the rules; it
        changes nothing on this account.
      </p>
      <div className="flex flex-col gap-3">
        <label className="flex flex-col gap-1.5 text-sm font-medium">
          What is wrong
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as FeedbackKind)}
            className="rounded-md border border-line-strong bg-surface-raised px-3 py-1.5 text-sm font-normal"
          >
            {FEEDBACK_KINDS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <Textarea
          label={kind === "other" ? "Note (required)" : "Note (optional)"}
          rows={2}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
        <div>
          <Button
            variant="secondary"
            onClick={submit}
            disabled={saving || (kind === "other" && !note.trim())}
          >
            {saving ? "Sending…" : "Send feedback"}
          </Button>
        </div>
        {done ? <Alert tone="success">Thanks, feedback saved.</Alert> : null}
        {error ? <Alert tone="danger">{error}</Alert> : null}
      </div>
    </Card>
  );
}

/** "Not a trigger" on one signal: feedback that points at that signal. */
export function FlagSignalButton({
  companyId,
  signalId,
  onSent,
}: {
  companyId: string;
  signalId: string;
  onSent: () => void;
}) {
  const [state, setState] = useState<"idle" | "saving" | "done" | "error">(
    "idle",
  );
  if (state === "done") {
    return <span className="text-xs text-fg-muted">Flagged, thanks.</span>;
  }
  return (
    <button
      type="button"
      disabled={state === "saving"}
      onClick={async () => {
        setState("saving");
        try {
          await sendFeedback(companyId, {
            kind: "not_a_trigger",
            target_type: "signal",
            target_id: signalId,
          });
          setState("done");
          onSent();
        } catch {
          setState("error");
        }
      }}
      className="text-xs text-fg-muted underline-offset-2 hover:text-fg hover:underline disabled:opacity-50"
    >
      {state === "error" ? "Could not flag, try again" : "Not a trigger"}
    </button>
  );
}
