"use client";

import { useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextField } from "@/components/ui/TextField";
import { errorMessage } from "@/lib/format";

import {
  addContact,
  archiveContact,
  type Contact,
  type ContactRole,
  findEmail,
  findPeople,
  setContactRole,
} from "./peopleApi";
import { RankingPanel } from "./RankingPanel";

const ROLE_LABEL: Record<string, string> = {
  primary: "Primary target",
  secondary: "Secondary target",
};

/** Potential decision-makers (Brief sections 8 and 14). Added by hand or found through the connected provider. */
export function PeopleCard({
  companyId,
  contacts,
  onChange,
}: {
  companyId: string;
  contacts: Contact[];
  onChange: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [found, setFound] = useState<string | null>(null);

  async function run(key: string, action: () => Promise<unknown>) {
    setBusy(key);
    setError(null);
    try {
      await action();
      onChange();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card title="People">
      {contacts.length === 0 ? (
        <p className="text-sm text-fg-muted">
          No one added yet. Add the people you would contact, then mark the
          primary and secondary target. They are needed before a message can be
          written.
        </p>
      ) : (
        <ul className="flex flex-col gap-4 text-sm">
          {contacts.map((contact) => (
            <li key={contact.id}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{contact.name}</span>
                {ROLE_LABEL[contact.role] ? (
                  <Badge tone="info">{ROLE_LABEL[contact.role]}</Badge>
                ) : null}
              </div>
              <p className="text-fg-muted">
                {contact.title || "No title"}
                {contact.email ? ` · ${contact.email}` : ""}
              </p>
              {contact.relevance_reason ? (
                <p className="mt-0.5 text-xs text-fg-muted">
                  Why: {contact.relevance_reason}
                </p>
              ) : null}
              <div className="mt-1.5 flex flex-wrap gap-3 text-xs">
                {(["primary", "secondary"] as const).map((role) =>
                  contact.role === role ? (
                    <button
                      key={role}
                      type="button"
                      disabled={busy !== null}
                      className="text-fg-muted underline-offset-2 hover:text-fg hover:underline"
                      onClick={() =>
                        run(contact.id, () =>
                          setContactRole(companyId, contact.id, "none"),
                        )
                      }
                    >
                      Remove {role}
                    </button>
                  ) : (
                    <button
                      key={role}
                      type="button"
                      disabled={busy !== null}
                      className="text-accent underline-offset-2 hover:underline"
                      onClick={() =>
                        run(contact.id, () =>
                          setContactRole(companyId, contact.id, role),
                        )
                      }
                    >
                      Make {role}
                    </button>
                  ),
                )}
                {contact.email ? null : (
                  <button
                    type="button"
                    disabled={busy !== null}
                    className="text-accent underline-offset-2 hover:underline"
                    onClick={() =>
                      run(contact.id, () => findEmail(companyId, contact.id))
                    }
                  >
                    Find email
                  </button>
                )}
                <button
                  type="button"
                  disabled={busy !== null}
                  className="text-fg-muted underline-offset-2 hover:text-fg hover:underline"
                  onClick={() =>
                    run(contact.id, () => archiveContact(companyId, contact.id))
                  }
                >
                  Remove person
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {error ? (
        <Alert tone="danger" className="mt-3">
          {error}
        </Alert>
      ) : null}
      {found && !error ? (
        <p className="mt-3 text-sm text-fg-muted">{found}</p>
      ) : null}
      {contacts.length > 0 ? (
        <div className="mt-4">
          <RankingPanel companyId={companyId} onApplied={onChange} />
        </div>
      ) : null}
      <div className="mt-4">
        {adding ? (
          <AddPerson
            companyId={companyId}
            onDone={() => {
              setAdding(false);
              onChange();
            }}
            onCancel={() => setAdding(false)}
          />
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setAdding(true)}
            >
              Add a person
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={busy !== null}
              onClick={() =>
                run("find", async () => {
                  const result = await findPeople(companyId);
                  setFound(
                    `${result.added.length} added, ${result.skipped} already listed. ${result.note}`.trim(),
                  );
                })
              }
            >
              {busy === "find" ? "Searching…" : "Find people"}
            </Button>
          </div>
        )}
      </div>
    </Card>
  );
}

function AddPerson({
  companyId,
  onDone,
  onCancel,
}: {
  companyId: string;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [title, setTitle] = useState("");
  const [email, setEmail] = useState("");
  const [reason, setReason] = useState("");
  const [role, setRole] = useState<ContactRole>("none");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await addContact(companyId, {
        name: name.trim(),
        title: title.trim(),
        email: email.trim(),
        relevance_reason: reason.trim(),
        role,
      });
      onDone();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-md border border-line p-4">
      <TextField
        label="Name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        required
      />
      <TextField
        label="Title"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="Managing Director"
      />
      <TextField
        label="Email (optional)"
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
      />
      <TextField
        label="Why this person"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        hint="One line, shown with the person."
      />
      <label className="flex flex-col gap-1.5 text-sm font-medium">
        Role
        <select
          value={role}
          onChange={(e) => setRole(e.target.value as ContactRole)}
          className="rounded-md border border-line-strong bg-surface-raised px-3 py-1.5 text-sm font-normal"
        >
          <option value="none">Not a target yet</option>
          <option value="primary">Primary target</option>
          <option value="secondary">Secondary target</option>
        </select>
      </label>
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <div className="flex gap-2">
        <Button onClick={save} disabled={saving || !name.trim()}>
          {saving ? "Saving…" : "Save person"}
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
