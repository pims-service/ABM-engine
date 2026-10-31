"use client";

import { type FormEvent, useId, useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { Select } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/Textarea";
import { TextField } from "@/components/ui/TextField";
import { useAccess } from "@/features/access/AccessProvider";

import {
  type Connection,
  type ConnectionCategory,
  type ConnectionCreateInput,
  type ConnectionUpdateInput,
  createConnection,
  updateConnection,
} from "./api";
import { CATEGORIES, hintsFor, KNOWN_PROVIDERS } from "./categories";
import { CredentialsEditor, newCredentialRow } from "./CredentialsEditor";
import { mapApiError } from "./formErrors";
import { GuidedConnectionForm } from "./GuidedConnectionForm";
import { providerPreset } from "./presets";
import {
  configToText,
  type CredentialRow,
  type CredentialRowErrors,
  credentialsBody,
  parseConfig,
  validateCredentialRows,
  validateName,
  validateProvider,
} from "./validation";

/**
 * "Add connection" (no `connection`) or "Edit connection" (name and settings only: the category
 * and provider are fixed, and credentials have their own dialog).
 */
export function ConnectionFormDialog({
  open,
  clientId,
  connection,
  providerKey,
  connectedCategories = [],
  onClose,
  onSaved,
}: {
  open: boolean;
  clientId: string;
  connection?: Connection | null;
  /** A provider with a ready-made form; without it the custom (raw) form opens. */
  providerKey?: string;
  connectedCategories?: readonly ConnectionCategory[];
  onClose: () => void;
  onSaved: (connection: Connection) => void;
}) {
  const preset = providerKey ? providerPreset(providerKey) : undefined;
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={
        connection
          ? "Edit connection"
          : preset
            ? `Connect ${preset.label}`
            : "Custom connection"
      }
      className="max-w-xl"
    >
      {/* Mounted only while open, so every field (and any secret) starts empty each time. */}
      {!connection && preset ? (
        <GuidedConnectionForm
          clientId={clientId}
          provider={preset}
          connectedCategories={connectedCategories}
          onClose={onClose}
          onSaved={onSaved}
        />
      ) : (
        <ConnectionForm
          clientId={clientId}
          connection={connection}
          onClose={onClose}
          onSaved={onSaved}
        />
      )}
    </Modal>
  );
}

function ConnectionForm({
  clientId,
  connection,
  onClose,
  onSaved,
}: {
  clientId: string;
  connection?: Connection | null;
  onClose: () => void;
  onSaved: (connection: Connection) => void;
}) {
  const access = useAccess();
  const providerListId = useId();
  const editing = Boolean(connection);

  const [category, setCategory] = useState<ConnectionCategory>(
    connection?.category ?? "llm",
  );
  const [provider, setProvider] = useState(connection?.provider ?? "");
  const [name, setName] = useState(connection?.name ?? "");
  const [configText, setConfigText] = useState(
    configToText(connection?.config),
  );
  const [rows, setRows] = useState<CredentialRow[]>(() => [
    newCredentialRow("api_key"),
  ]);
  const [rowErrors, setRowErrors] = useState<
    Record<number, CredentialRowErrors>
  >({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [cleared, setCleared] = useState(false);
  const [pending, setPending] = useState(false);

  const hints = hintsFor(provider);

  function clearSecrets() {
    setRows((current) => current.map((row) => ({ ...row, value: "" })));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const next: Record<string, string> = {};
    const nameError = validateName(name);
    if (nameError) next.name = nameError;
    if (!editing) {
      const providerError = validateProvider(provider);
      if (providerError) next.provider = providerError;
    }
    const config = parseConfig(configText);
    if (!config.ok) next.config = config.error;

    const filled = rows.some((row) => row.value.trim() !== "");
    let credentialErrors: Record<number, CredentialRowErrors> = {};
    if (!editing && filled) {
      const checked = validateCredentialRows(rows);
      credentialErrors = checked.rows;
      if (checked.form) next.credentials = checked.form;
    }
    setErrors(next);
    setRowErrors(credentialErrors);
    setCleared(false);
    if (
      Object.keys(next).length > 0 ||
      Object.keys(credentialErrors).length > 0 ||
      !config.ok
    ) {
      return;
    }

    setPending(true);
    setFormError(null);
    try {
      let saved: Connection;
      if (connection) {
        const patch: ConnectionUpdateInput = {};
        if (name.trim() !== connection.name) patch.name = name.trim();
        if (configToText(config.value) !== configToText(connection.config)) {
          patch.config = config.value;
        }
        saved =
          Object.keys(patch).length > 0
            ? await updateConnection(connection.id, patch)
            : connection;
      } else {
        const body: ConnectionCreateInput = {
          client: clientId,
          category,
          status: "active",
          provider,
          name: name.trim(),
          config: config.value,
          ...(filled ? { credentials: credentialsBody(rows) } : {}),
        };
        saved = await createConnection(body);
      }
      onSaved(saved);
    } catch (error) {
      const denied = access.noteError(error, "manage", clientId);
      const mapped = mapApiError(error, denied);
      setErrors(mapped.fields);
      setFormError(mapped.form);
    } finally {
      // The request has gone out (or failed): the secrets must not linger in state.
      if (!editing && filled) {
        clearSecrets();
        setCleared(true);
      }
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
        {editing ? (
          <p className="text-sm text-fg-muted">
            {CATEGORIES.find((item) => item.value === category)?.label} ·{" "}
            <span className="font-mono">{provider}</span> (the category and
            provider cannot be changed)
          </p>
        ) : (
          <>
            <Select
              label="Category"
              value={category}
              error={errors.category}
              onChange={(event) =>
                setCategory(event.target.value as ConnectionCategory)
              }
            >
              {CATEGORIES.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </Select>
            <TextField
              label="Provider key"
              value={provider}
              list={providerListId}
              error={errors.provider}
              hint="Lower-case letters, digits and underscores, for example anthropic or gemini."
              maxLength={64}
              required
              markRequired
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              onChange={(event) => setProvider(event.target.value.trim())}
            />
            <datalist id={providerListId}>
              {KNOWN_PROVIDERS[category].map((key) => (
                <option key={key} value={key} />
              ))}
            </datalist>
          </>
        )}
        <TextField
          label="Name"
          value={name}
          error={errors.name}
          hint="Shown in lists. Unique per provider among active connections."
          maxLength={200}
          required
          markRequired
          autoComplete="off"
          onChange={(event) => setName(event.target.value)}
        />
        <Textarea
          label="Settings (JSON)"
          value={configText}
          error={errors.config}
          hint={
            hints.configHint ??
            'Optional, non-secret settings as a JSON object, for example {"model": "fast"}.'
          }
          rows={4}
          className="font-mono"
          spellCheck={false}
          autoComplete="off"
          onChange={(event) => setConfigText(event.target.value)}
        />
        {editing ? (
          <p className="text-sm text-fg-muted">
            Credentials are changed separately with Credentials in the list.
          </p>
        ) : (
          <>
            <CredentialsEditor
              rows={rows}
              errors={rowErrors}
              disabled={pending}
              onChange={setRows}
            />
            {errors.credentials ? (
              <p className="text-xs font-medium text-danger-fg">
                {errors.credentials}
              </p>
            ) : null}
          </>
        )}
        {cleared ? (
          <Alert tone="warning">
            For safety the secret values were cleared. Enter them again to
            retry.
          </Alert>
        ) : null}
        {formError ? <Alert tone="danger">{formError}</Alert> : null}
      </div>
      <div className="mt-5 flex flex-wrap items-center justify-end gap-2">
        <div className="flex gap-2">
          <Button variant="secondary" disabled={pending} onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={pending}>
            {pending ? "Saving…" : editing ? "Save changes" : "Add connection"}
          </Button>
        </div>
      </div>
    </form>
  );
}
