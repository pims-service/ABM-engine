"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import { PageHeader } from "@/components/ui/PageHeader";
import { listCampaignCompanies } from "@/features/companies/api";
import { useOptionalSelection } from "@/features/selection/SelectionProvider";
import { useApiQuery } from "@/lib/hooks/useApiQuery";

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
}) {
  return (
    <div className="rounded-lg border border-line bg-surface-raised p-5">
      <p className="text-xs font-medium text-fg-muted">{label}</p>
      <p className="mt-2 text-2xl font-semibold tracking-tight">{value}</p>
      {hint ? <p className="mt-1 text-xs text-fg-muted">{hint}</p> : null}
    </div>
  );
}

const STEPS = [
  {
    title: "Pick a client and campaign",
    body: "Use the switcher at the top. Everything you see belongs to that campaign.",
    href: "/campaigns",
    cta: "Pick a campaign",
  },
  {
    title: "Add target companies",
    body: "Enter a name and website, or import a list.",
    href: "/companies",
    cta: "Add a company",
  },
  {
    title: "Research a company",
    body: "Read its public website and see what it says, with the source and time of each page.",
    href: "/companies",
    cta: "Open a company",
  },
] as const;

/** `/dashboard`: real counts for the current selection, and the three steps to get going. */
export function Dashboard() {
  const selection = useOptionalSelection();
  const campaign = selection?.campaign ?? null;
  const companies = useApiQuery(
    `dashboard-companies:${campaign?.id ?? "none"}`,
    (signal) => listCampaignCompanies(campaign!.id, signal),
    { enabled: campaign !== null },
  );

  return (
    <>
      <PageHeader
        title="Dashboard"
        description={
          campaign
            ? `${selection?.client?.name ?? "Client"} · ${campaign.name}`
            : "Pick a client and a campaign to get started."
        }
      />
      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="Clients" value={selection?.clients.length ?? "—"} />
        <Stat
          label="Campaigns"
          value={selection?.clientId ? selection.campaigns.length : "—"}
          hint={selection?.client ? `for ${selection.client.name}` : undefined}
        />
        <Stat
          label="Companies"
          value={campaign ? (companies.data?.count ?? "…") : "—"}
          hint={campaign ? `in ${campaign.name}` : undefined}
        />
      </div>

      <h2 className="mb-3 mt-10 text-sm font-semibold">Get started</h2>
      <ol className="grid gap-4 sm:grid-cols-3">
        {STEPS.map((step, index) => (
          <li
            key={step.title}
            className="flex flex-col rounded-lg border border-line bg-surface-raised p-5"
          >
            <span className="text-xs font-medium text-fg-muted">
              Step {index + 1}
            </span>
            <p className="mt-2 text-sm font-semibold">{step.title}</p>
            <p className="mt-1 flex-1 text-sm text-fg-muted">{step.body}</p>
            <Link
              href={step.href}
              className="mt-4 text-sm font-medium text-accent underline-offset-2 hover:underline"
            >
              {step.cta} &rarr;
            </Link>
          </li>
        ))}
      </ol>
    </>
  );
}
