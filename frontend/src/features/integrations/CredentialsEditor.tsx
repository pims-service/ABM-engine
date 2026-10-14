"use client";

import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/TextField";

import {
  type CredentialRow,
  type CredentialRowErrors,
  MAX_CREDENTIAL_VALUE_LENGTH,
  MAX_CREDENTIALS,
} from "./validation";

let nextRowId = 1;

/** A fresh, empty row. Ids only key React lists; they are not sent anywhere. */
export function newCredentialRow(name = ""): CredentialRow {
  return { id: nextRowId++, name, value: "" };
}

/**
 * Write-only credential fields: a name and a secret per row. The secret input is a password
 * field that is never pre-filled and never revealed; browsers are told not to autofill or save
 * it (`autocomplete="new-password"`). The parent owns the rows and must clear them after submit.
 */
export function CredentialsEditor({
  rows,
  errors = {},
  disabled,
  onChange,
}: {
  rows: readonly CredentialRow[];
  errors?: Record<number, CredentialRowErrors>;
  disabled?: boolean;
  onChange: (rows: CredentialRow[]) => void;
}) {
  function update(id: number, patch: Partial<CredentialRow>) {
    onChange(rows.map((row) => (row.id === id ? { ...row, ...patch } : row)));
  }

  return (
    <fieldset className="flex flex-col gap-3" disabled={disabled}>
      <legend className="mb-1 text-sm font-medium text-fg">Credentials</legend>
      <p className="text-xs text-fg-muted">
        Secrets are encrypted when saved and can never be shown again.
      </p>
      {rows.map((row, index) => (
        <div
          key={row.id}
          className="grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)_auto] sm:items-start"
        >
          <TextField
            label={`Credential ${index + 1} name`}
            value={row.name}
            error={errors[row.id]?.name}
            maxLength={64}
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            onChange={(event) => update(row.id, { name: event.target.value })}
          />
          <TextField
            label={`Credential ${index + 1} secret`}
            type="password"
            value={row.value}
            error={errors[row.id]?.value}
            maxLength={MAX_CREDENTIAL_VALUE_LENGTH + 1}
            autoComplete="new-password"
            autoCapitalize="none"
            spellCheck={false}
            data-lpignore="true"
            data-1p-ignore="true"
            onChange={(event) => update(row.id, { value: event.target.value })}
          />
          {rows.length > 1 ? (
            <Button
              variant="ghost"
              size="sm"
              className="sm:mt-7"
              aria-label={`Remove credential ${index + 1}`}
              onClick={() => onChange(rows.filter((r) => r.id !== row.id))}
            >
              Remove
            </Button>
          ) : null}
        </div>
      ))}
      {rows.length < MAX_CREDENTIALS ? (
        <div>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => onChange([...rows, newCredentialRow()])}
          >
            Add another credential
          </Button>
        </div>
      ) : null}
    </fieldset>
  );
}
