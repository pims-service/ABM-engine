"use client";

import { type FormEvent, useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Select";
import { TextField } from "@/components/ui/TextField";
import { useAccess } from "@/features/access/AccessProvider";

import {
  type Connection,
  type ConnectionCategory,
  createConnection,
} from "./api";
import { mapApiError } from "./formErrors";
import { type PresetField, type ProviderPreset } from "./presets";

const MIN_SECRET_LENGTH = 8;

function initialValues(preset: ProviderPreset): Record<string, string> {
  return Object.fromEntries(
    preset.fields.map((field) => [field.name, field.options?.[0]?.value ?? ""]),
  );
}

/**
 * A short form for one provider: only the inputs it needs, in plain words. Settings and secrets
 * are sent in the right places, so nobody types JSON or credential names. A provider that sets
 * up several categories with one key (SignalHire) creates the ones that are still missing.
 */
export function GuidedConnectionForm({
  clientId,
  provider,
  connectedCategories,
  onClose,
  onSaved,
}: {
  clientId: string;
  provider: ProviderPreset;
  /** Categories this provider already has an active connection for. */
  connectedCategories: readonly ConnectionCategory[];
  onClose: () => void;
  onSaved: (connection: Connection) => void;
}) {
  const access = useAccess();
  const [values, setValues] = useState(() => initialValues(provider));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [cleared, setCleared] = useState(false);
  const [pending, setPending] = useState(false);

  function validate(): Record<string, string> {
    const next: Record<string, string> = {};
    for (const field of provider.fields) {
      const value = (values[field.name] ?? "").trim();
      if (field.required && !value) {
        next[field.name] = `Enter the ${field.label.toLowerCase()}.`;
      } else if (
        field.kind === "secret" &&
        value &&
        value.length < MIN_SECRET_LENGTH
      ) {
        next[field.name] = "That looks too short to be a key.";
      }
    }
    return next;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const found = validate();
    setErrors(found);
    setCleared(false);
    if (Object.keys(found).length > 0) return;

    const config: Record<string, unknown> = {};
    const credentials: Record<string, string> = {};
    for (const field of provider.fields) {
      const value = (values[field.name] ?? "").trim();
      if (!value) continue;
      if (field.kind === "secret") credentials[field.name] = value;
      else if (field.options && value === field.options[0]?.value) continue;
      else config[field.name] = value;
    }

    const missing = provider.categories.filter(
      (category) => !connectedCategories.includes(category),
    );
    setPending(true);
    setFormError(null);
    try {
      let saved: Connection | null = null;
      for (const category of missing) {
        saved = await createConnection({
          client: clientId,
          category,
          status: "active",
          provider: provider.key,
          name: provider.label,
          config,
          credentials,
        });
      }
      if (saved) onSaved(saved);
    } catch (error) {
      const denied = access.noteError(error, "manage", clientId);
      const mapped = mapApiError(error, denied);
      const own: Record<string, string> = {};
      const other: string[] = [];
      for (const [field, message] of Object.entries(mapped.fields)) {
        if (provider.fields.some((f) => f.name === field)) {
          own[field] = message;
        } else {
          other.push(message);
        }
      }
      setErrors(own);
      setFormError([mapped.form, ...other].filter(Boolean).join(" ") || null);
    } finally {
      // The request has gone out (or failed): secrets must not linger in state.
      setValues((current) =>
        Object.fromEntries(
          Object.entries(current).map(([key, value]) => [
            key,
            provider.fields.find((f) => f.name === key)?.kind === "secret"
              ? ""
              : value,
          ]),
        ),
      );
      setCleared(provider.fields.some((f) => f.kind === "secret"));
      setPending(false);
    }
  }

  function render(field: PresetField) {
    const value = values[field.name] ?? "";
    const set = (next: string) =>
      setValues((current) => ({ ...current, [field.name]: next }));
    if (field.options) {
      return (
        <Select
          key={field.name}
          label={field.label}
          value={value}
          hint={field.hint}
          error={errors[field.name]}
          onChange={(event) => set(event.target.value)}
        >
          {field.options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
      );
    }
    return (
      <TextField
        key={field.name}
        label={field.label}
        value={value}
        placeholder={field.placeholder}
        hint={field.hint}
        error={errors[field.name]}
        required={field.required}
        markRequired={field.required}
        autoComplete={field.kind === "secret" ? "new-password" : "off"}
        autoCapitalize="none"
        spellCheck={false}
        type={field.kind === "secret" ? "password" : "text"}
        data-lpignore={field.kind === "secret" ? "true" : undefined}
        data-1p-ignore={field.kind === "secret" ? "true" : undefined}
        onChange={(event) => set(event.target.value)}
      />
    );
  }

  return (
    <form
      onSubmit={(event) => void submit(event)}
      noValidate
      autoComplete="off"
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm text-fg-muted">{provider.blurb}</p>
        {provider.fields.map(render)}
        {provider.keyLink ? (
          <a
            href={provider.keyLink.href}
            target="_blank"
            rel="noopener noreferrer"
            className="-mt-2 text-xs text-accent underline-offset-2 hover:underline"
          >
            {provider.keyLink.text} ↗
          </a>
        ) : null}
        {cleared && formError ? (
          <Alert tone="warning">
            For safety the key was cleared. Enter it again to retry.
          </Alert>
        ) : null}
        {formError ? <Alert tone="danger">{formError}</Alert> : null}
      </div>
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="secondary" disabled={pending} onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          {pending ? "Connecting…" : "Connect"}
        </Button>
      </div>
    </form>
  );
}
