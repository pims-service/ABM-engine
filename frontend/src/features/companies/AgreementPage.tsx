"use client";

import Link from "next/link";
import { useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button, buttonStyles } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { Table, Td, Th } from "@/components/ui/Table";
import { TextField } from "@/components/ui/TextField";
import { useOptionalSelection } from "@/features/selection/SelectionProvider";
import { errorMessage } from "@/lib/format";
import { useApiQuery } from "@/lib/hooks/useApiQuery";

import { downloadFeedback, getAgreement, KIND_LABELS } from "./agreementApi";

const CHOICES = ["add", "hold", "skip"] as const;

/** `/companies/agreement`: how often people agree with the AI, and what they flagged. */
export function AgreementPage() {
  const selection = useOptionalSelection();
  const campaign = selection?.campaign ?? null;
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [error, setError] = useState<string | null>(null);

  const report = useApiQuery(
    `agreement:${campaign?.id}:${start}:${end}`,
    (signal) => getAgreement(campaign!.id, start, end, signal),
    { enabled: campaign !== null },
  );

  if (!campaign) {
    return (
      <>
        <PageHeader title="AI and your decisions" />
        <EmptyState
          title="No campaign selected"
          description="Pick a client and a campaign in the header."
        />
      </>
    );
  }
  const data = report.data;
  const cell = (ai: string, human: string) =>
    data?.matrix.find((c) => c.ai === ai && c.human === human)?.count ?? 0;

  return (
    <>
      <PageHeader
        title="AI and your decisions"
        description="How often your latest decision matched the AI, and what was flagged. Use it to improve the prompts and rules."
        actions={
          <>
            <Button
              variant="secondary"
              onClick={() =>
                downloadFeedback(campaign.id, start, end).catch((cause) =>
                  setError(errorMessage(cause)),
                )
              }
            >
              Export feedback
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
      <div className="mb-4 flex flex-wrap gap-3">
        <TextField
          label="From"
          type="date"
          value={start}
          onChange={(event) => setStart(event.target.value)}
        />
        <TextField
          label="To"
          type="date"
          value={end}
          onChange={(event) => setEnd(event.target.value)}
        />
      </div>
      {report.error ? (
        <Alert tone="danger">{errorMessage(report.error)}</Alert>
      ) : !data ? (
        <Skeleton className="h-48 w-full" />
      ) : (
        <div className="flex flex-col gap-4">
          <Card>
            <p className="text-3xl font-semibold">
              {data.rate === null ? "–" : `${Math.round(data.rate * 100)}%`}
            </p>
            <p className="text-sm text-fg-muted">
              {data.compared === 0
                ? "No decisions made against an AI recommendation in this period."
                : `You matched the AI on ${data.agreed} of ${data.compared} accounts.`}
              {data.without_ai > 0
                ? ` ${data.without_ai} more were decided without a recommendation and are not counted.`
                : ""}
            </p>
          </Card>
          <Card>
            <h2 className="mb-2 font-medium">
              AI recommendation vs your decision
            </h2>
            <Table caption="AI recommendation against your decision">
              <thead>
                <tr>
                  <Th>AI said</Th>
                  {CHOICES.map((human) => (
                    <Th key={human}>You: {human}</Th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {CHOICES.map((ai) => (
                  <tr key={ai}>
                    <Td className="font-medium">{ai}</Td>
                    {CHOICES.map((human) => (
                      <Td key={human}>{cell(ai, human)}</Td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </Table>
          </Card>
          <Card>
            <h2 className="mb-2 font-medium">Flags raised</h2>
            {data.feedback.length === 0 ? (
              <p className="text-sm text-fg-muted">Nothing was flagged.</p>
            ) : (
              <Table caption="Flags raised and how often">
                <thead>
                  <tr>
                    <Th>Flag</Th>
                    <Th>Times</Th>
                  </tr>
                </thead>
                <tbody>
                  {data.feedback.map((row) => (
                    <tr key={row.kind}>
                      <Td>{KIND_LABELS[row.kind] ?? row.kind}</Td>
                      <Td>{row.count}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </Card>
        </div>
      )}
    </>
  );
}
