"use client";

import { useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Table, Td, Th } from "@/components/ui/Table";
import { TextField } from "@/components/ui/TextField";
import { ApiError } from "@/lib/api";
import { errorMessage } from "@/lib/format";
import { useApiQuery } from "@/lib/hooks/useApiQuery";

import {
  addMember,
  getMembers,
  removeMember,
  ROLES,
  setMemberRole,
  type TeamRole,
} from "./teamApi";

/** Who can work on this client and as what. Only client admins see it. */
export function TeamCard({ clientId }: { clientId: string }) {
  const { data, error, loading, reload } = useApiQuery(
    `team:${clientId}`,
    (signal) => getMembers(clientId, signal),
  );
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  async function run(key: string, action: () => Promise<unknown>) {
    setBusy(key);
    setActionError(null);
    try {
      await action();
      reload();
    } catch (cause) {
      setActionError(errorMessage(cause));
    } finally {
      setBusy(null);
    }
  }

  // Only client admins may see the team: for everyone else the card is simply not there.
  if (error instanceof ApiError && error.status === 403) return null;

  return (
    <Card title="Team">
      <p className="mb-3 text-sm text-fg-muted">
        People who can work on this client. Roles decide what they can do: a
        viewer can look, a reviewer can decide, a manager can edit and analyze,
        an admin can also manage the team.
      </p>
      {error ? (
        <Alert tone="danger">Could not load the team. {error.message}</Alert>
      ) : loading && !data ? (
        <p role="status" className="text-sm text-fg-muted">
          Loading the team…
        </p>
      ) : (
        <Table caption="Team">
          <thead>
            <tr>
              <Th>Person</Th>
              <Th>Role</Th>
              <Th>
                <span className="sr-only">Actions</span>
              </Th>
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((member) => (
              <tr key={member.user_id}>
                <Td>
                  <span className="font-medium">
                    {member.name || member.email}
                  </span>
                  {member.is_you ? <Badge className="ml-2">You</Badge> : null}
                  {member.name ? (
                    <span className="block text-xs text-fg-muted">
                      {member.email}
                    </span>
                  ) : null}
                </Td>
                <Td>
                  <select
                    aria-label={`Role of ${member.email}`}
                    value={member.role}
                    disabled={busy !== null}
                    onChange={(e) =>
                      run(member.user_id, () =>
                        setMemberRole(
                          clientId,
                          member.user_id,
                          e.target.value as TeamRole,
                        ),
                      )
                    }
                    className="rounded-md border border-line-strong bg-surface-raised px-3 py-1.5 text-sm"
                  >
                    {ROLES.map((role) => (
                      <option key={role.value} value={role.value}>
                        {role.label}
                      </option>
                    ))}
                  </select>
                </Td>
                <Td className="text-right">
                  <button
                    type="button"
                    disabled={busy !== null}
                    className="text-sm text-fg-muted underline-offset-2 hover:text-fg hover:underline"
                    onClick={() => {
                      if (
                        window.confirm(
                          `Remove ${member.email}? They lose access to this client immediately.`,
                        )
                      ) {
                        void run(member.user_id, () =>
                          removeMember(clientId, member.user_id),
                        );
                      }
                    }}
                  >
                    Remove
                  </button>
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {actionError ? (
        <Alert tone="danger" className="mt-3">
          {actionError}
        </Alert>
      ) : null}
      <div className="mt-4">
        {adding ? (
          <AddPerson
            clientId={clientId}
            onDone={() => {
              setAdding(false);
              reload();
            }}
            onCancel={() => setAdding(false)}
          />
        ) : (
          <Button variant="secondary" size="sm" onClick={() => setAdding(true)}>
            Add a person
          </Button>
        )}
      </div>
    </Card>
  );
}

function AddPerson({
  clientId,
  onDone,
  onCancel,
}: {
  clientId: string;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<TeamRole>("reviewer");
  const [password, setPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await addMember(clientId, {
        email: email.trim(),
        role,
        name: name.trim(),
        password,
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
        label="Email"
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        required
      />
      <label className="flex flex-col gap-1.5 text-sm font-medium">
        Role
        <select
          value={role}
          onChange={(e) => setRole(e.target.value as TeamRole)}
          className="rounded-md border border-line-strong bg-surface-raised px-3 py-1.5 text-sm font-normal"
        >
          {ROLES.map((r) => (
            <option key={r.value} value={r.value}>
              {r.label}: {r.hint}
            </option>
          ))}
        </select>
      </label>
      <TextField
        label="Name (new people only)"
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <TextField
        label="Starting password (new people only)"
        type="password"
        autoComplete="new-password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        hint="Leave empty if they already have an account. A new person can change the password after signing in."
      />
      {error ? <Alert tone="danger">{error}</Alert> : null}
      <div className="flex gap-2">
        <Button onClick={save} disabled={saving || !email.trim()}>
          {saving ? "Adding…" : "Add to the team"}
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
