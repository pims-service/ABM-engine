"use client";

import { useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Textarea } from "@/components/ui/Textarea";
import { TextField } from "@/components/ui/TextField";
import { errorMessage, formatDate, formatDateTime } from "@/lib/format";

import type { ResearchCard } from "./cardApi";
import {
  approveMessage,
  type Channel,
  editMessage,
  exportMessage,
  generateMessages,
  type OutreachMessage,
} from "./messagesApi";
import type { Contact } from "./peopleApi";

const LANGUAGE_LABEL: Record<string, string> = { en: "English", ar: "Arabic" };
const CHANNEL_LABEL: Record<string, string> = {
  linkedin: "LinkedIn",
  email: "Email",
};
const STATUS_TONE = {
  draft: "neutral",
  approved: "success",
  exported: "info",
} as const;

/** Drafts for the primary contact, approved by a person (Brief sections 9, 11 and 12). */
export function OutreachCard({
  card,
  contacts,
  messages,
  onChange,
}: {
  card: ResearchCard;
  contacts: Contact[];
  messages: OutreachMessage[];
  onChange: () => void;
}) {
  const decided = card.decision?.decision === "add";
  const primary = contacts.find((c) => c.role === "primary");
  const [channels, setChannels] = useState<Channel[]>(["linkedin", "email"]);
  const [language, setLanguage] = useState(card.outreach_languages[0] ?? "en");
  const [writing, setWriting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rejected, setRejected] = useState<string[]>([]);

  async function write() {
    setWriting(true);
    setError(null);
    setRejected([]);
    try {
      const result = await generateMessages(
        card.company.id,
        channels,
        language,
      );
      setRejected(result.rejected);
      onChange();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setWriting(false);
    }
  }

  const blocker = !decided
    ? "Choose ADD in your decision above to write messages."
    : !card.has_angle
      ? "No outreach angle yet. Run the analysis first."
      : !primary
        ? "Add a person and make them the primary target to write messages."
        : null;

  return (
    <Card title="Outreach">
      <p className="mb-3 text-sm text-fg-muted">
        Drafts use only the research above. A person approves each one; nothing
        is sent from here.
      </p>
      {blocker ? (
        <p className="text-sm text-fg-muted">{blocker}</p>
      ) : (
        <div className="flex flex-col gap-3">
          <p className="text-sm">
            For <span className="font-medium">{primary?.name}</span>
            {primary?.title ? `, ${primary.title}` : ""}
          </p>
          <div className="flex flex-wrap items-end gap-4">
            <fieldset className="flex gap-4 text-sm">
              <legend className="sr-only">Channels</legend>
              {(["linkedin", "email"] as const).map((channel) => (
                <label key={channel} className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={channels.includes(channel)}
                    onChange={(e) =>
                      setChannels(
                        e.target.checked
                          ? [...channels, channel]
                          : channels.filter((c) => c !== channel),
                      )
                    }
                  />
                  {CHANNEL_LABEL[channel]}
                </label>
              ))}
            </fieldset>
            <label className="flex flex-col gap-1.5 text-sm font-medium">
              Language
              <select
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                className="rounded-md border border-line-strong bg-surface-raised px-3 py-1.5 text-sm font-normal"
              >
                {card.outreach_languages.map((code) => (
                  <option key={code} value={code}>
                    {LANGUAGE_LABEL[code] ?? code}
                  </option>
                ))}
              </select>
            </label>
            <Button onClick={write} disabled={writing || channels.length === 0}>
              {writing
                ? "Writing…"
                : messages.length > 0
                  ? "Write new drafts"
                  : "Write drafts"}
            </Button>
          </div>
        </div>
      )}
      {error ? (
        <Alert tone="danger" className="mt-3">
          {error}
        </Alert>
      ) : null}
      {rejected.length > 0 ? (
        <Alert tone="warning" className="mt-3">
          Some drafts were refused because they were not grounded in the
          research: {rejected.join("; ")}
        </Alert>
      ) : null}
      {messages.length > 0 ? (
        <ul className="mt-5 flex flex-col gap-4">
          {messages.map((message) => (
            <MessageItem
              key={message.id}
              companyId={card.company.id}
              message={message}
              onChange={onChange}
            />
          ))}
        </ul>
      ) : null}
    </Card>
  );
}

function MessageItem({
  companyId,
  message,
  onChange,
}: {
  companyId: string;
  message: OutreachMessage;
  onChange: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [body, setBody] = useState(message.body);
  const [subject, setSubject] = useState(message.subject);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      setEditing(false);
      onChange();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    const text = message.subject
      ? `Subject: ${message.subject}\n\n${message.body}`
      : message.body;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setError("Could not copy. Select the text and copy it by hand.");
    }
  }

  return (
    <li className="rounded-md border border-line p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">
          {CHANNEL_LABEL[message.channel] ?? message.channel}
          {message.is_followup ? " follow-up" : ""}
        </span>
        <Badge tone={STATUS_TONE[message.status as keyof typeof STATUS_TONE]}>
          {message.status}
        </Badge>
        <span className="text-xs text-fg-muted">
          to {message.contact_name} ·{" "}
          {LANGUAGE_LABEL[message.language] ?? message.language}
        </span>
      </div>
      {editing ? (
        <div className="mt-3 flex flex-col gap-3">
          {message.channel === "email" && !message.is_followup ? (
            <TextField
              label="Subject"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
            />
          ) : null}
          <Textarea
            label="Message"
            rows={6}
            value={body}
            onChange={(e) => setBody(e.target.value)}
            hint="Saving makes a new draft that needs approval again."
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={busy || !body.trim()}
              onClick={() =>
                run(() =>
                  editMessage(
                    companyId,
                    message.id,
                    body.trim(),
                    message.channel === "email" && !message.is_followup
                      ? subject.trim()
                      : undefined,
                  ),
                )
              }
            >
              Save as new draft
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setEditing(false);
                setBody(message.body);
                setSubject(message.subject);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <>
          {message.subject ? (
            <p className="mt-2 font-medium">{message.subject}</p>
          ) : null}
          <p className="mt-1 whitespace-pre-line" dir="auto">
            {message.body}
          </p>
        </>
      )}
      {message.evidence.length > 0 ? (
        <p className="mt-2 text-xs text-fg-muted">
          Based on:{" "}
          {message.evidence
            .map((e) => `${e.type_label} (${formatDate(e.event_date)})`)
            .join(", ")}
        </p>
      ) : (
        <p className="mt-2 text-xs text-fg-muted">
          No event mentioned: this draft uses the angle only.
        </p>
      )}
      {message.approved_at ? (
        <p className="mt-1 text-xs text-fg-muted">
          Approved by {message.approved_by} on{" "}
          {formatDateTime(message.approved_at)}
        </p>
      ) : null}
      {error ? (
        <Alert tone="danger" className="mt-2">
          {error}
        </Alert>
      ) : null}
      {!editing ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {message.status === "draft" ? (
            <Button
              size="sm"
              disabled={busy}
              onClick={() => run(() => approveMessage(companyId, message.id))}
            >
              Approve
            </Button>
          ) : null}
          {message.status !== "exported" ? (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => setEditing(true)}
            >
              Edit
            </Button>
          ) : null}
          <Button size="sm" variant="secondary" onClick={copy}>
            {copied ? "Copied" : "Copy"}
          </Button>
          {message.status === "approved" ? (
            <Button
              size="sm"
              variant="secondary"
              disabled={busy}
              onClick={() => run(() => exportMessage(companyId, message.id))}
            >
              Mark as exported
            </Button>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}
