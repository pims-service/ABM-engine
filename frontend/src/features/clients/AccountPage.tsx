"use client";

import { useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { TextField } from "@/components/ui/TextField";
import { useOptionalAuth } from "@/lib/auth/AuthProvider";
import { errorMessage } from "@/lib/format";

import { changePassword } from "./teamApi";

/** `/account`: who you are and a way to change your password. */
export function AccountPage() {
  const user = useOptionalAuth()?.user;
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const mismatch = again !== "" && again !== next;

  async function save() {
    setSaving(true);
    setError(null);
    setDone(false);
    try {
      await changePassword(current, next);
      setCurrent("");
      setNext("");
      setAgain("");
      setDone(true);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Your account"
        description={user ? `Signed in as ${user.email}.` : undefined}
      />
      <Card title="Change your password" className="max-w-md">
        <div className="flex flex-col gap-3">
          <TextField
            label="Current password"
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
          <TextField
            label="New password"
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            hint="At least 12 characters is a good habit. Avoid common words."
          />
          <TextField
            label="New password again"
            type="password"
            autoComplete="new-password"
            value={again}
            onChange={(e) => setAgain(e.target.value)}
            error={mismatch ? "The two passwords do not match." : undefined}
          />
          {error ? <Alert tone="danger">{error}</Alert> : null}
          {done ? <Alert tone="success">Password changed.</Alert> : null}
          <div>
            <Button
              onClick={save}
              disabled={saving || !current || !next || next !== again}
            >
              {saving ? "Saving…" : "Change password"}
            </Button>
          </div>
        </div>
      </Card>
    </>
  );
}
