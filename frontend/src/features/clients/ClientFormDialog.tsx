"use client";

import { type FormEvent, useId, useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { TextArea } from "@/components/ui/TextArea";
import { TextField } from "@/components/ui/TextField";
import { useAccess } from "@/features/access/AccessProvider";
import { ApiError } from "@/lib/api";
import { errorMessage } from "@/lib/format";

import { type Client, createClient, updateClient } from "./api";

/**
 * Create (no `client`) or edit (`client`) dialog: name and notes. Validation errors from the API
 * show under the matching field; any other failure shows above the buttons.
 */
export function ClientFormDialog({
  open,
  client,
  onClose,
  onSaved,
}: {
  open: boolean;
  client?: Client | null;
  onClose: () => void;
  onSaved: (client: Client) => void;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={client ? "Edit client" : "New client"}
    >
      {/* Mounted only while open, so the fields start fresh every time. */}
      <ClientForm client={client} onClose={onClose} onSaved={onSaved} />
    </Modal>
  );
}

function ClientForm({
  client,
  onClose,
  onSaved,
}: {
  client?: Client | null;
  onClose: () => void;
  onSaved: (client: Client) => void;
}) {
  const formId = useId();
  const access = useAccess();
  const [name, setName] = useState(client?.name ?? "");
  const [notes, setNotes] = useState(client?.notes ?? "");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setFieldErrors({ name: "Enter a name for the client." });
      return;
    }
    setPending(true);
    setFieldErrors({});
    setFormError(null);
    try {
      const saved = client
        ? await updateClient(client.id, { name: trimmed, notes })
        : await createClient({ name: trimmed, notes });
      onSaved(saved);
    } catch (error) {
      const denied = access.noteError(
        error,
        client ? "edit" : "manage",
        client?.id,
      );
      const fields: Record<string, string> = {};
      if (error instanceof ApiError) {
        for (const [field, messages] of Object.entries(error.fieldErrors)) {
          fields[field] = messages.join(" ");
        }
      }
      if (Object.keys(fields).length > 0) setFieldErrors(fields);
      else if (denied) {
        setFormError("You do not have permission to do this.");
      } else setFormError(errorMessage(error));
    } finally {
      setPending(false);
    }
  }

  return (
    <form id={formId} onSubmit={(event) => void submit(event)} noValidate>
      <div className="flex flex-col gap-4">
        <TextField
          label="Name"
          value={name}
          maxLength={200}
          required
          autoComplete="off"
          error={fieldErrors.name}
          onChange={(event) => setName(event.target.value)}
        />
        <TextArea
          label="Notes"
          value={notes}
          error={fieldErrors.notes}
          hint="Optional. Context for your team, not shown to the client."
          onChange={(event) => setNotes(event.target.value)}
        />
        {formError ? <Alert tone="danger">{formError}</Alert> : null}
      </div>
      <div className="mt-5 flex flex-wrap justify-end gap-2">
        <Button variant="secondary" disabled={pending} onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          {pending ? "Saving…" : client ? "Save changes" : "Create client"}
        </Button>
      </div>
    </form>
  );
}
