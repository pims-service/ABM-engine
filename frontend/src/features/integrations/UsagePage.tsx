"use client";

import Link from "next/link";
import { useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button, buttonStyles } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Table, Td, Th } from "@/components/ui/Table";
import { TextField } from "@/components/ui/TextField";
import { useOptionalSelection } from "@/features/selection/SelectionProvider";
import { ApiError } from "@/lib/api";
import { errorMessage } from "@/lib/format";
import { useApiQuery } from "@/lib/hooks/useApiQuery";

import { useConnections } from "./api";
import {
  type Budget,
  getBudget,
  getUsageThisMonth,
  saveBudget,
} from "./usageApi";

const thousands = (value: string | null | undefined) =>
  value == null
    ? "0"
    : Number(value).toLocaleString("en", { maximumFractionDigits: 1 });

/** `/settings/usage`: what the AI used this month and a monthly limit per connection. */
export function UsagePage() {
  const client = useOptionalSelection()?.client ?? null;
  const header = (
    <PageHeader
      title="AI usage and limits"
      description={
        client
          ? `This month for ${client.name}.`
          : "Pick a client in the header to see its AI usage."
      }
      actions={
        <Link
          href="/settings/integrations"
          className={buttonStyles({ variant: "secondary" })}
        >
          Integrations
        </Link>
      }
    />
  );
  if (!client) {
    return (
      <>
        {header}
        <EmptyState
          title="No client selected"
          description="Usage belongs to a client. Choose one in the header."
        />
      </>
    );
  }
  return (
    <>
      {header}
      <Usage clientId={client.id} />
    </>
  );
}

function Usage({ clientId }: { clientId: string }) {
  const usage = useApiQuery(`usage:${clientId}`, (signal) =>
    getUsageThisMonth(clientId, signal),
  );
  const connections = useConnections(clientId, { archived: "false" });

  if (
    (usage.error instanceof ApiError && usage.error.status === 403) ||
    (connections.error instanceof ApiError && connections.error.status === 403)
  ) {
    return (
      <EmptyState
        title="No access"
        description="Usage and limits are for managers and admins of this client."
      />
    );
  }
  const rows = Array.isArray(usage.data) ? usage.data : [];
  const llm = (connections.data?.results ?? []).filter(
    (c) => c.category === "llm",
  );

  return (
    <div className="flex flex-col gap-5">
      <Card title="This month">
        {usage.error ? (
          <Alert tone="danger">
            Could not load usage. {usage.error.message}
          </Alert>
        ) : rows.length === 0 ? (
          <p className="text-sm text-fg-muted">
            Nothing has been used yet this month.
          </p>
        ) : (
          <Table caption="Usage this month">
            <thead>
              <tr>
                <Th>Provider</Th>
                <Th>Calls</Th>
                <Th>Failed</Th>
                <Th>Tokens in</Th>
                <Th>Tokens out</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={`${row.provider}-${index}`}>
                  <Td className="font-medium">{row.provider ?? "all"}</Td>
                  <Td>{row.calls}</Td>
                  <Td>{row.error_calls}</Td>
                  <Td>{(row.input_tokens ?? 0).toLocaleString("en")}</Td>
                  <Td>{(row.output_tokens ?? 0).toLocaleString("en")}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      {llm.length === 0 ? (
        <Card title="Monthly limit">
          <p className="text-sm text-fg-muted">
            Connect an AI provider under Integrations to set a monthly limit.
          </p>
        </Card>
      ) : (
        llm.map((connection) => (
          <BudgetCard
            key={connection.id}
            connectionId={connection.id}
            name={connection.name}
            onSaved={usage.reload}
          />
        ))
      )}
    </div>
  );
}

function BudgetCard({
  connectionId,
  name,
  onSaved,
}: {
  connectionId: string;
  name: string;
  onSaved: () => void;
}) {
  const { data, error, reload } = useApiQuery(
    `budget:${connectionId}`,
    (signal) => getBudget(connectionId, signal),
  );
  const [limit, setLimit] = useState<string | null>(null);
  const [warn, setWarn] = useState<string | null>(null);
  const [hardStop, setHardStop] = useState<boolean | null>(null);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  if (error) {
    return (
      <Card title={`Monthly limit: ${name}`}>
        <Alert tone="danger">Could not load the limit. {error.message}</Alert>
      </Card>
    );
  }
  if (!data) return null;
  const budget: Budget = data;
  const limitValue =
    limit ?? (budget.credit_limit ? String(Number(budget.credit_limit)) : "");
  const warnValue = warn ?? String(budget.warn_at_percent);
  const stopValue = hardStop ?? budget.hard_stop;

  async function save() {
    setSaving(true);
    setProblem(null);
    setSaved(false);
    try {
      await saveBudget(connectionId, {
        creditLimit: limitValue.trim() === "" ? null : limitValue.trim(),
        warnAtPercent: Number(warnValue) || 80,
        hardStop: stopValue,
      });
      setLimit(null);
      setWarn(null);
      setHardStop(null);
      setSaved(true);
      reload();
      onSaved();
    } catch (cause) {
      setProblem(errorMessage(cause));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card title={`Monthly limit: ${name}`}>
      <p className="mb-3 text-sm text-fg-muted">
        Used this month:{" "}
        <span className="font-medium text-fg">
          {thousands(budget.used_credits)}
        </span>{" "}
        thousand tokens
        {budget.credit_limit
          ? ` of ${thousands(budget.credit_limit)}`
          : " (no limit set)"}
        .{" "}
        {budget.state === "deny" ? (
          <Badge tone="danger">Limit reached: new analyses are blocked</Badge>
        ) : budget.state === "warn" ? (
          <Badge tone="warning">Close to the limit</Badge>
        ) : null}
      </p>
      <div className="flex max-w-md flex-col gap-3">
        <TextField
          label="Monthly limit (thousand tokens)"
          inputMode="decimal"
          value={limitValue}
          onChange={(e) => setLimit(e.target.value)}
          hint="Leave empty for no limit. About 3 thousand tokens per analyzed company is typical."
        />
        <TextField
          label="Warn me at (percent)"
          inputMode="numeric"
          value={warnValue}
          onChange={(e) => setWarn(e.target.value.replace(/\D/g, ""))}
        />
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={stopValue}
            onChange={(e) => setHardStop(e.target.checked)}
          />
          Stop all AI calls when the limit is reached
        </label>
        {problem ? <Alert tone="danger">{problem}</Alert> : null}
        {saved ? <Alert tone="success">Limit saved.</Alert> : null}
        <div>
          <Button onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save limit"}
          </Button>
        </div>
      </div>
    </Card>
  );
}
