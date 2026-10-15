"use client";

import { useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextField } from "@/components/ui/TextField";
import { errorMessage } from "@/lib/format";

import type { ResearchCard } from "./cardApi";
import {
  type CommercialInput,
  type Row,
  saveCommercialTeam,
  type TeamKey,
} from "./commercialApi";

const COLUMNS: ReadonlyArray<readonly [TeamKey, string]> = [
  ["employees", "Company"],
  ["sales", "Sales"],
  ["bd", "Business dev."],
  ["marketing", "Marketing"],
  ["commercial", "Commercial"],
];
const ROWS = [
  ["now", "Now"],
  ["3", "3 months ago"],
  ["6", "6 months ago"],
  ["12", "12 months ago"],
] as const;

type Grid = Record<string, Partial<Record<TeamKey, string>>>;

const percent = (value: number | null | undefined) =>
  value == null ? "Not known" : `${value > 0 ? "+" : ""}${value}%`;

/** Team sizes and growth (Brief section 5). Typed in by hand until a data provider is connected. */
export function CommercialTeamCard({
  card,
  onChange,
}: {
  card: ResearchCard;
  onChange: (card: ResearchCard) => void;
}) {
  const research = card.research;
  const [editing, setEditing] = useState(false);
  const known = research
    ? [
        research.employee_count,
        research.sales_headcount,
        research.bd_headcount,
        research.marketing_headcount,
        research.commercial_partnerships_headcount,
      ].some((value) => value != null)
    : false;

  return (
    <Card title="Commercial team">
      {known && research ? (
        <>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
            {(
              [
                ["Employees", research.employee_count],
                ["Sales", research.sales_headcount],
                ["Business development", research.bd_headcount],
                ["Marketing", research.marketing_headcount],
                [
                  "Commercial / partnerships",
                  research.commercial_partnerships_headcount,
                ],
              ] as const
            ).map(([label, value]) => (
              <div key={label} className="flex flex-col py-1">
                <dt className="text-xs text-fg-muted">{label}</dt>
                <dd>{value ?? "Not found"}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-3 text-sm">
            <span className="text-fg-muted">Company growth: </span>3 months{" "}
            {percent(research.headcount_change_3m)} · 6 months{" "}
            {percent(research.headcount_change_6m)} · 12 months{" "}
            {percent(research.headcount_change_12m)}
          </p>
          <p className="mt-1 text-xs text-fg-muted">
            Source: {research.source.name}
          </p>
        </>
      ) : (
        <p className="text-sm text-fg-muted">
          No team numbers yet. Add what you found (for example from the
          company&apos;s public page) and the growth is calculated for you.
        </p>
      )}
      <div className="mt-4">
        {editing ? (
          <Form
            companyId={card.company.id}
            onSaved={(next) => {
              setEditing(false);
              onChange(next);
            }}
            onCancel={() => setEditing(false)}
          />
        ) : (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setEditing(true)}
          >
            {known ? "Update the numbers" : "Enter the numbers"}
          </Button>
        )}
      </div>
    </Card>
  );
}

function toRow(grid: Grid[string] | undefined): Row {
  const row: Row = {};
  for (const [key] of COLUMNS) {
    const text = grid?.[key]?.trim();
    if (text) row[key] = Number(text);
  }
  return row;
}

function Form({
  companyId,
  onSaved,
  onCancel,
}: {
  companyId: string;
  onSaved: (card: ResearchCard) => void;
  onCancel: () => void;
}) {
  const [note, setNote] = useState("");
  const [link, setLink] = useState("");
  const [asOf, setAsOf] = useState("");
  const [grid, setGrid] = useState<Grid>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function set(row: string, key: TeamKey, value: string) {
    setGrid((current) => ({
      ...current,
      [row]: { ...current[row], [key]: value.replace(/\D/g, "") },
    }));
  }

  async function save() {
    setSaving(true);
    setError(null);
    const input: CommercialInput = {
      source_note: note.trim(),
      source_url: link.trim(),
      as_of: asOf || undefined,
      now: toRow(grid.now),
      history: (["3", "6", "12"] as const)
        .map((months) => ({
          months_ago: Number(months) as 3 | 6 | 12,
          ...toRow(grid[months]),
        }))
        .filter((entry) => Object.keys(entry).length > 1),
    };
    try {
      onSaved(await saveCommercialTeam(companyId, input));
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-md border border-line p-4">
      <TextField
        label="Where do these numbers come from?"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="Company page, read on 14 Oct"
        required
      />
      <div className="flex flex-wrap gap-3">
        <div className="min-w-56 flex-1">
          <TextField
            label="Link (optional)"
            value={link}
            onChange={(e) => setLink(e.target.value)}
          />
        </div>
        <div className="w-44">
          <TextField
            label="True on (default today)"
            type="date"
            value={asOf}
            onChange={(e) => setAsOf(e.target.value)}
          />
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Team sizes</caption>
          <thead>
            <tr>
              <th className="py-1 pr-3 text-xs font-medium text-fg-muted">
                <span className="sr-only">Period</span>
              </th>
              {COLUMNS.map(([key, label]) => (
                <th
                  key={key}
                  className="px-1 py-1 text-xs font-medium text-fg-muted"
                >
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ROWS.map(([row, label]) => (
              <tr key={row}>
                <th scope="row" className="py-1 pr-3 text-xs font-medium">
                  {label}
                </th>
                {COLUMNS.map(([key, column]) => (
                  <td key={key} className="px-1 py-1">
                    <input
                      inputMode="numeric"
                      aria-label={`${column}, ${label}`}
                      value={grid[row]?.[key] ?? ""}
                      onChange={(e) => set(row, key, e.target.value)}
                      className="w-20 rounded-md border border-line-strong bg-surface-raised px-2 py-1 text-sm"
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-fg-muted">
        Fill only what you know. The growth for a period is calculated from the
        numbers you give; a period you leave empty stays empty.
      </p>
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <div className="flex gap-2">
        <Button onClick={save} disabled={saving || !note.trim()}>
          {saving ? "Saving…" : "Save numbers"}
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
