"use client";

import { useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextField } from "@/components/ui/TextField";
import { errorMessage } from "@/lib/format";

import {
  type Connection,
  type ConnectionCategory,
  createConnection,
  testConnection,
  updateConnection,
} from "./api";

/** One provider that finds people and their emails, set up in one step for both categories. */
interface ContactProvider {
  key: string;
  name: string;
  summary: string;
  keyHelp: string;
  /** False while the adapter is not built: the key can be saved, but nothing uses it. */
  built: boolean;
}

const PROVIDERS: readonly ContactProvider[] = [
  {
    key: "signalhire",
    name: "SignalHire",
    summary:
      "Finds the people at a company by title (no credits, a daily search quota) and reveals an approved person's work email (one credit each).",
    keyHelp: "Your API key from Integrations & API in your SignalHire account.",
    built: true,
  },
  {
    key: "mawsool",
    name: "Mawsool",
    summary:
      "B2B contacts for the Middle East. The connection is not built yet: you can save the key now, but nothing is looked up until its API documentation is added.",
    keyHelp: "Your Mawsool API key.",
    built: false,
  },
];

const CATEGORIES: readonly ConnectionCategory[] = [
  "people_data",
  "email_enrichment",
];

/** Connect SignalHire (or Mawsool) once and use it for finding people and emails. */
export function ProviderSetup({
  clientId,
  connections,
  onChanged,
}: {
  clientId: string;
  connections: Connection[];
  onChanged: () => void;
}) {
  return (
    <section aria-labelledby="contact-providers" className="mb-6">
      <h2 id="contact-providers" className="text-lg font-semibold">
        Find people and emails
      </h2>
      <p className="mb-3 text-sm text-fg-muted">
        Connect a provider once. It is used for &ldquo;Find people&rdquo; and
        &ldquo;Find email&rdquo; on a company. Emails are only looked up for
        accounts you approved.
      </p>
      <div className="grid gap-3 md:grid-cols-2">
        {PROVIDERS.map((provider) => (
          <ProviderCard
            key={provider.key}
            provider={provider}
            clientId={clientId}
            connections={connections.filter(
              (c) => c.provider === provider.key && !c.archived_at,
            )}
            onChanged={onChanged}
          />
        ))}
      </div>
    </section>
  );
}

function ProviderCard({
  provider,
  clientId,
  connections,
  onChanged,
}: {
  provider: ContactProvider;
  clientId: string;
  connections: Connection[];
  onChanged: () => void;
}) {
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState<"save" | "check" | null>(null);
  const [message, setMessage] = useState<{
    tone: "success" | "danger" | "info";
    text: string;
  } | null>(null);

  const have = new Set(connections.map((c) => c.category));
  const connected = CATEGORIES.every((c) => have.has(c));

  async function save() {
    setBusy("save");
    setMessage(null);
    try {
      for (const category of CATEGORIES) {
        const existing = connections.find((c) => c.category === category);
        if (existing) {
          await updateConnection(existing.id, {
            credentials: { api_key: secret },
          });
        } else {
          await createConnection({
            client: clientId,
            category,
            provider: provider.key,
            name: provider.name,
            status: "active",
            config: {},
            credentials: { api_key: secret },
          });
        }
      }
      setSecret("");
      setMessage({ tone: "success", text: `${provider.name} key saved.` });
      onChanged();
    } catch (cause) {
      setMessage({ tone: "danger", text: errorMessage(cause) });
    } finally {
      setBusy(null);
    }
  }

  async function check() {
    setBusy("check");
    setMessage(null);
    try {
      const first = connections[0];
      if (!first) return;
      const result = await testConnection(first.id);
      setMessage({
        tone: result.ok ? "success" : "danger",
        text:
          result.message ||
          (result.ok ? "The key works." : "The check failed."),
      });
      onChanged();
    } catch (cause) {
      setMessage({ tone: "danger", text: errorMessage(cause) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-medium">{provider.name}</h3>
        {connected ? (
          <Badge tone="success">Key saved</Badge>
        ) : (
          <Badge tone="neutral">Not connected</Badge>
        )}
      </div>
      <p className="mt-1 text-sm text-fg-muted">{provider.summary}</p>
      <div className="mt-3">
        <TextField
          label={connected ? "Replace the API key" : "API key"}
          type="password"
          autoComplete="off"
          value={secret}
          onChange={(event) => setSecret(event.target.value)}
          hint={provider.keyHelp}
        />
      </div>
      {message ? (
        <Alert tone={message.tone} className="mt-3">
          {message.text}
        </Alert>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-2">
        <Button
          size="sm"
          disabled={busy !== null || secret.trim().length < 8}
          onClick={save}
        >
          {busy === "save" ? "Saving…" : "Save key"}
        </Button>
        {connected && provider.built ? (
          <Button
            size="sm"
            variant="secondary"
            disabled={busy !== null}
            onClick={check}
          >
            {busy === "check" ? "Checking…" : "Check key"}
          </Button>
        ) : null}
      </div>
    </Card>
  );
}
