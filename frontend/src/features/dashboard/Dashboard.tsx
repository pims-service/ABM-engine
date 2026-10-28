"use client";

import Link from "next/link";

import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import {
  type AccountSummary,
  getAccountSummary,
} from "@/features/companies/accountsApi";
import { useOptionalSelection } from "@/features/selection/SelectionProvider";
import { errorMessage } from "@/lib/format";
import { useApiQuery } from "@/lib/hooks/useApiQuery";

interface Todo {
  text: string;
  href?: string;
  link?: string;
}

function count(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** What is worth doing next in this campaign, most urgent first. Only what applies is listed. */
function todos(s: AccountSummary): Todo[] {
  if (s.total === 0) {
    return [
      {
        text: "Add the companies you want to qualify.",
        href: "/companies/import",
        link: "Import a CSV",
      },
    ];
  }
  const list: Todo[] = [];
  if (s.needs_review > 0) {
    list.push({
      text: `${count(s.needs_review, "account is", "accounts are")} waiting for your decision.`,
      href: "/companies/review",
      link: "Review",
    });
  }
  if (s.failed > 0) {
    list.push({
      text: `${count(s.failed, "analysis", "analyses")} failed.`,
      href: "/companies",
      link: "See which",
    });
  }
  if (s.not_assessed > 0) {
    list.push({
      text: `${count(s.not_assessed, "company has", "companies have")} not been analyzed.`,
      href: "/companies",
      link: "Analyze",
    });
  }
  if (s.analyzing > 0) {
    list.push({
      text: `${count(s.analyzing, "analysis is", "analyses are")} running.`,
    });
  }
  if (s.human_add > 0) {
    list.push({
      text: `${count(s.human_add, "approved account", "approved accounts")}: find the right person, write the messages, then export or push to the CRM.`,
      href: "/companies",
      link: "Open companies",
    });
  }
  return list;
}

/** One line on where the campaign stands. */
function standing(s: AccountSummary): string {
  if (s.total === 0) return "No companies yet.";
  return [
    count(s.total, "company", "companies"),
    `${s.strong} strong fit`,
    `${s.trigger_yes} with a trigger`,
    `${s.human_add} approved`,
    count(s.meetings, "meeting", "meetings"),
  ].join(" · ");
}

/** `/dashboard`: where the current campaign stands and what to do next. */
export function Dashboard() {
  const selection = useOptionalSelection();
  const campaign = selection?.campaign ?? null;
  const summary = useApiQuery(
    `dashboard-summary:${campaign?.id ?? "none"}`,
    (signal) => getAccountSummary(campaign!.id, signal),
    { enabled: campaign !== null },
  );

  if (!campaign) {
    return (
      <>
        <PageHeader title="Dashboard" />
        <EmptyState
          title="No campaign selected"
          description="Choose a client and a campaign in the switcher at the top. Every page follows that choice."
        />
      </>
    );
  }

  const s = summary.data;
  return (
    <>
      <PageHeader
        title="Dashboard"
        description={`${selection?.client?.name ?? "Client"} · ${campaign.name}`}
      />
      {summary.error ? (
        <p className="text-sm text-danger-fg">
          Could not load the numbers. {errorMessage(summary.error)}
        </p>
      ) : !s ? (
        <p className="text-sm text-fg-muted">Loading…</p>
      ) : (
        <>
          <p className="text-sm">{standing(s)}</p>
          <h2 className="mb-2 mt-8 text-sm font-semibold">To do</h2>
          {todos(s).length === 0 ? (
            <p className="text-sm text-fg-muted">
              Nothing waiting. Every analyzed account has a decision.
            </p>
          ) : (
            <ul className="divide-y divide-line rounded-lg border border-line bg-surface-raised">
              {todos(s).map((todo) => (
                <li
                  key={todo.text}
                  className="flex items-center justify-between gap-4 px-4 py-3 text-sm"
                >
                  <span>{todo.text}</span>
                  {todo.href ? (
                    <Link
                      href={todo.href}
                      className="shrink-0 font-medium text-accent underline-offset-2 hover:underline"
                    >
                      {todo.link}
                    </Link>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </>
  );
}
