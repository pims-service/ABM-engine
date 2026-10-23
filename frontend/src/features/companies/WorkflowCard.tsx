"use client";

import { Card } from "@/components/ui/Card";

import { type ResearchCard } from "./cardApi";

/** What is done and what comes next for an approved account (Brief section 12). */
export function WorkflowCard({ card }: { card: ResearchCard }) {
  if (card.workflow.length === 0) return null;
  const next = card.workflow.find((step) => !step.done);
  return (
    <Card title="Next steps">
      <p className="mb-3 text-sm text-fg-muted">
        {next
          ? `Next: ${next.title.toLowerCase()}.`
          : "Everything is done for this account."}
      </p>
      <ol className="flex flex-col gap-2 text-sm">
        {card.workflow.map((step) => (
          <li key={step.key} className="flex gap-2">
            <span
              aria-hidden="true"
              className={step.done ? "text-success-fg" : "text-fg-muted"}
            >
              {step.done ? "✓" : "○"}
            </span>
            <span>
              <span className={step.done ? "" : "font-medium"}>
                {step.title}
              </span>
              <span className="block text-xs text-fg-muted">{step.detail}</span>
            </span>
          </li>
        ))}
      </ol>
    </Card>
  );
}
