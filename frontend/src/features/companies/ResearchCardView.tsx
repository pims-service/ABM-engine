"use client";

import Link from "next/link";
import { type ReactNode, useEffect, useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { StatusPill } from "@/components/ui/StatusPill";
import { Textarea } from "@/components/ui/Textarea";
import { errorMessage, formatDate, formatDateTime } from "@/lib/format";

import {
  type ActivityItem,
  type DecisionChoice,
  recordDecision,
  type ResearchCard,
} from "./cardApi";
import { CommercialTeamCard } from "./CommercialTeamCard";
import {
  ActivityCard,
  FeedbackCard,
  FlagSignalButton,
} from "./FeedbackAndActivity";
import { type OutreachMessage } from "./messagesApi";
import { OutreachCard } from "./OutreachCard";
import { type Contact } from "./peopleApi";
import { PeopleCard } from "./PeopleCard";
import { ResearchHistoryCard } from "./ResearchHistoryCard";

const POLL_MS = 3000;

function Fact({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex justify-between gap-4 border-t border-line py-2 first:border-t-0">
      <dt className="text-fg-muted">{label}</dt>
      <dd className="text-right">
        {value === null || value === undefined || value === "" ? (
          <span className="text-fg-muted">Not found</span>
        ) : (
          value
        )}
      </dd>
    </div>
  );
}

function List({ items, warn }: { items: string[]; warn?: boolean }) {
  return (
    <ul className="flex flex-col gap-1.5">
      {items.map((item) => (
        <li key={item} className="flex gap-2">
          <span aria-hidden="true" className="text-fg-muted">
            {warn ? "!" : "•"}
          </span>
          <span>{item}</span>
        </li>
      ))}
    </ul>
  );
}

/** The account research card (Brief section 10): the main review screen. */
export function ResearchCardView({
  card,
  activity,
  contacts,
  messages,
  onMessages,
  onPeople,
  onChange,
  reload,
  onActivity,
}: {
  card: ResearchCard;
  activity: ActivityItem[];
  contacts: Contact[];
  messages: OutreachMessage[];
  onMessages: () => void;
  onPeople: () => void;
  onChange: (card: ResearchCard) => void;
  reload: () => void;
  onActivity: () => void;
}) {
  const { company, research, trigger, icp, recommendation, decision } = card;
  const running =
    card.analysis?.status === "queued" || card.analysis?.status === "running";

  // While an analysis job runs, look again every few seconds.
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(reload, POLL_MS);
    return () => clearInterval(timer);
  }, [running, reload]);

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <div className="flex flex-wrap items-center gap-2">
          {icp ? (
            <StatusPill
              kind="icp"
              value={icp.fit as "strong" | "medium" | "weak"}
            />
          ) : (
            <Badge>Not assessed</Badge>
          )}
          <StatusPill kind="trigger" value={trigger.triggered ? "yes" : "no"} />
          {recommendation ? (
            <StatusPill
              kind="ai"
              value={recommendation.status as "add" | "hold" | "skip"}
            />
          ) : null}
          {decision ? (
            <Badge tone={decision.decision === "skip" ? "neutral" : "info"}>
              You: {decision.decision.toUpperCase()}
            </Badge>
          ) : (
            <Badge>Awaiting your decision</Badge>
          )}
          {!trigger.triggered && icp ? (
            <span className="text-xs text-fg-muted">
              No current trigger. A strong fit is still worth contacting.
            </span>
          ) : null}
        </div>
        {recommendation ? (
          <p className="mt-3 text-sm">{recommendation.explanation}</p>
        ) : null}
        {card.analysis?.status === "failed" ? (
          <Alert tone="warning" className="mt-3">
            The last analysis did not finish. {card.analysis.error_summary}{" "}
            {card.analysis.error_summary.includes("Integrations") ? (
              <Link
                href="/settings/integrations"
                className="font-medium underline"
              >
                Open integrations
              </Link>
            ) : null}
          </Alert>
        ) : null}
        {running ? (
          <p role="status" className="mt-3 text-sm text-fg-muted">
            Analysis running. This page updates by itself.
          </p>
        ) : null}
      </Card>

      <DecisionBar
        card={card}
        onChange={(next) => {
          onChange(next);
          onActivity();
        }}
      />

      <div className="grid items-start gap-5 lg:grid-cols-2">
        <div className="flex flex-col gap-5">
          <Card title="Why this fit">
            {icp ? (
              <div className="flex flex-col gap-4 text-sm">
                <List items={icp.reasons} />
                {icp.concerns.length > 0 ? (
                  <div>
                    <p className="mb-1.5 text-xs font-medium text-fg-muted">
                      Concerns
                    </p>
                    <List items={icp.concerns} warn />
                  </div>
                ) : null}
                <p className="text-xs text-fg-muted">
                  Judged on {formatDateTime(icp.created_at)} against campaign
                  rules version {icp.profile_version}.
                </p>
              </div>
            ) : (
              <p className="text-sm text-fg-muted">
                Run the analysis to see why this company fits or not.
              </p>
            )}
          </Card>

          <Card title="Recommended angle">
            {recommendation?.outreach_angle ? (
              <p className="text-sm">{recommendation.outreach_angle}</p>
            ) : (
              <p className="text-sm text-fg-muted">
                No angle yet. It is only suggested when the facts support one.
              </p>
            )}
          </Card>

          <OutreachCard
            card={card}
            contacts={contacts}
            messages={messages}
            onChange={() => {
              onMessages();
              onActivity();
            }}
          />

          <Card title="Signals">
            {trigger.signals.length === 0 ? (
              <p className="text-sm text-fg-muted">
                No current signals. That is fine: a trigger is not required to
                qualify.
              </p>
            ) : (
              <ul className="flex flex-col gap-4 text-sm">
                {trigger.signals.map((signal) => (
                  <li key={signal.id}>
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone="trigger-yes">{signal.type_label}</Badge>
                      <span className="text-xs text-fg-muted">
                        {formatDate(signal.event_date)}
                        {signal.expires_at
                          ? ` · current until ${formatDate(signal.expires_at)}`
                          : ""}
                      </span>
                    </div>
                    <p className="mt-1.5">{signal.evidence}</p>
                    <div className="mt-1">
                      <FlagSignalButton
                        companyId={company.id}
                        signalId={signal.id}
                        onSent={onActivity}
                      />
                    </div>
                    {signal.source.url ? (
                      <a
                        href={signal.source.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="mt-1 inline-block break-all text-xs text-accent underline-offset-2 hover:underline"
                      >
                        {signal.source.url}
                      </a>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        <div className="flex flex-col gap-5">
          <Card title="Company">
            {research ? (
              <>
                {research.description ? (
                  <p className="mb-3 text-sm">{research.description}</p>
                ) : null}
                <dl className="text-sm">
                  <Fact label="Industry" value={research.industry} />
                  <Fact label="Headquarters" value={research.headquarters} />
                  <Fact label="Employees" value={research.employee_count} />
                  <Fact
                    label="Business model"
                    value={
                      research.classification &&
                      research.classification !== "unknown"
                        ? research.classification.toUpperCase()
                        : research.business_model
                    }
                  />
                  <Fact
                    label="Products and services"
                    value={research.products_services}
                  />
                  <Fact
                    label="Target customers"
                    value={research.target_customers}
                  />
                </dl>
                <p className="mt-3 break-all text-xs text-fg-muted">
                  Source:{" "}
                  {research.source.url ? (
                    <a
                      href={research.source.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-accent underline-offset-2 hover:underline"
                    >
                      {research.source.url}
                    </a>
                  ) : (
                    research.source.name
                  )}{" "}
                  · read {formatDateTime(research.source.retrieved_at)}
                </p>
              </>
            ) : (
              <p className="text-sm text-fg-muted">
                Nothing researched yet for {company.name}.
              </p>
            )}
          </Card>

          <CommercialTeamCard card={card} onChange={onChange} />

          <PeopleCard
            companyId={company.id}
            contacts={contacts}
            onChange={onPeople}
          />

          <ResearchHistoryCard
            companyId={company.id}
            latestId={card.research?.researched_at ?? null}
          />

          <ActivityCard items={activity} />

          <FeedbackCard companyId={company.id} onSent={onActivity} />

          <Card title="Not available yet">
            <ul className="flex flex-col gap-2 text-sm text-fg-muted">
              {card.gaps.map((gap) => (
                <li key={gap}>{gap}</li>
              ))}
            </ul>
          </Card>
        </div>
      </div>
    </div>
  );
}

function DecisionBar({
  card,
  onChange,
}: {
  card: ResearchCard;
  onChange: (card: ResearchCard) => void;
}) {
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState<DecisionChoice | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { decision, recommendation } = card;

  async function choose(choice: DecisionChoice) {
    setSaving(choice);
    setError(null);
    try {
      onChange(await recordDecision(card.company.id, choice, note.trim()));
      setNote("");
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setSaving(null);
    }
  }

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-3">
        <span className="mr-1 text-sm font-medium">Your decision</span>
        {(["add", "hold", "skip"] as const).map((choice) => (
          <Button
            key={choice}
            variant={choice === "add" ? "primary" : "secondary"}
            disabled={saving !== null}
            aria-pressed={decision?.decision === choice}
            onClick={() => choose(choice)}
          >
            {saving === choice ? "Saving…" : choice.toUpperCase()}
          </Button>
        ))}
      </div>
      <div className="mt-3 max-w-xl">
        <Textarea
          label="Note (optional)"
          rows={2}
          value={note}
          onChange={(event) => setNote(event.target.value)}
          hint="Why you chose this. Kept with the decision."
        />
      </div>
      {error ? (
        <Alert tone="danger" className="mt-3">
          {error}
        </Alert>
      ) : null}
      {decision ? (
        <p className="mt-3 text-xs text-fg-muted">
          Latest: {decision.decision.toUpperCase()} by {decision.decided_by} on{" "}
          {formatDateTime(decision.decided_at)}
          {decision.agrees_with_ai === null
            ? ""
            : decision.agrees_with_ai
              ? " · agrees with the AI"
              : ` · differs from the AI (${recommendation?.status.toUpperCase()})`}
          {decision.note ? ` · “${decision.note}”` : ""}
        </p>
      ) : null}
      <p className="mt-2 text-xs text-fg-muted">
        The AI only recommends. Nothing is contacted until a person decides.
      </p>
    </Card>
  );
}
