"use client";

import { type FormEvent, useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { useAccess } from "@/features/access/AccessProvider";

import { type Connection, updateConnection } from "./api";
import { CredentialsEditor, newCredentialRow } from "./CredentialsEditor";
import { mapApiError } from "./formErrors";
import {
  type CredentialRow,
  type CredentialRowErrors,
  credentialsBody,
  validateCredentialRows,
} from "./validation";

/** The masked hint of stored credentials, for display (`api_key ****abcd`). Never a secret. */
export function HintList({ connection }: { connection: Connection }) {
  const entries = Object.entries(connection.credentials_hint ?? {});
  if (entries.length === 0) return null;
  return (
    <ul className="mt-1 flex flex-col gap-0.5 text-xs text-fg-muted">
      {entries.map(([key, hint]) => (
        <li key={key}>
          <span className="font-mono">{key}</span>{" "}
          <span className="font-mono">{hint}</span>
        </li>
      ))}
    </ul>
  );
}

/**
 * Replace the credentials of a connection. The stored values cannot be read, so the form starts
 * with the existing credential *names* and empty secrets: whatever is submitted replaces all of
 * them. "Clear credentials" hands over to the parent, which asks for confirmation.
 */
export function CredentialsDialog({
  open,
  connection,
  onClose,
  onSaved,
  onRequestClear,
}: {
  open: boolean;
  connection: Connection | null;
  onClose: () => void;
  onSaved: (connection: Connection) => void;
  onRequestClear: (connection: Connection) => void;
}) {
  return (
    <Modal
      open={open && connection !== null}
      onClose={onClose}
      title={
        connection?.has_credentials ? "Replace credentials" : "Set credentials"
      }
      description={connection ? connection.name : undefined}
      className="max-w-xl"
    >
      {connection ? (
        <CredentialsForm
          connection={connection}
          onClose={onClose}
          onSaved={onSaved}
          onRequestClear={onRequestClear}
        />
      ) : null}
    </Modal>
  );
}

function CredentialsForm({
  connection,
  onClose,
  onSaved,
  onRequestClear,
}: {
  connection: Connection;
  onClose: () => void;
  onSaved: (connection: Connection) => void;
  onRequestClear: (connection: Connection) => void;
}) {
  const access = useAccess();
  const [rows, setRows] = useState<CredentialRow[]>(() => {
    const names = Object.keys(connection.credentials_hint ?? {});
    return (names.length > 0 ? names : ["api_key"]).map((name) =>
      newCredentialRow(name),
    );
  });
  const [rowErrors, setRowErrors] = useState<
    Record<number, CredentialRowErrors>
  >({});
  const [formError, setFormError] = useState<string | null>(null);
  const [cleared, setCleared] = useState(false);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const checked = validateCredentialRows(rows);
    setRowErrors(checked.rows);
    setCleared(false);
    if (checked.form || Object.keys(checked.rows).length > 0) {
      setFormError(checked.form ?? null);
      return;
    }
    setPending(true);
    setFormError(null);
    try {
      const saved = await updateConnection(connection.id, {
        credentials: credentialsBody(rows),
      });
      onSaved(saved);
    } catch (error) {
      const denied = access.noteError(error, "manage", connection.client);
      const mapped = mapApiError(error, denied);
      setFormError(
        mapped.fields.credentials ?? mapped.form ?? "Could not save.",
      );
    } finally {
      // Secrets must not outlive the request.
      setRows((current) => current.map((row) => ({ ...row, value: "" })));
      setCleared(true);
      setPending(false);
    }
  }

  return (
    <form
      onSubmit={(event) => void submit(event)}
      noValidate
      autoComplete="off"
    >
      <div className="flex flex-col gap-4">
        {connection.has_credentials ? (
          <div className="rounded-md border border-line bg-surface-muted p-3 text-sm">
            <p>
              Credentials are stored and cannot be shown. Entering new ones
              replaces all of them.
            </p>
            <HintList connection={connection} />
          </div>
        ) : (
          <p className="text-sm text-fg-muted">
            No credentials are stored for this connection yet.
          </p>
        )}
        <CredentialsEditor
          rows={rows}
          errors={rowErrors}
          disabled={pending}
          onChange={setRows}
        />
        {cleared && formError ? (
          <Alert tone="warning">
            For safety the secret values were cleared. Enter them again to
            retry.
          </Alert>
        ) : null}
        {formError ? <Alert tone="danger">{formError}</Alert> : null}
      </div>
      <div className="mt-5 flex flex-wrap items-center justify-between gap-2">
        <div>
          {connection.has_credentials ? (
            <Button
              variant="ghost"
              disabled={pending}
              onClick={() => onRequestClear(connection)}
            >
              Clear credentials
            </Button>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" disabled={pending} onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={pending}>
            {pending
              ? "Saving…"
              : connection.has_credentials
                ? "Replace credentials"
                : "Save credentials"}
          </Button>
        </div>
      </div>
    </form>
  );
}
