"use client";

import { type ReactNode, useState } from "react";

import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useAccess } from "@/features/access/AccessProvider";
import { useOptionalSelection } from "@/features/selection/SelectionProvider";

import { archiveClient, type Client, restoreClient } from "./api";
import { ClientFormDialog } from "./ClientFormDialog";

type Pending =
  | { kind: "form"; client: Client | null }
  | { kind: "archive" | "restore"; client: Client }
  | null;

export interface ClientActions {
  /** Open the create dialog. */
  create: () => void;
  edit: (client: Client) => void;
  /** Ask for confirmation, then archive. */
  archive: (client: Client) => void;
  restore: (client: Client) => void;
  /** Result of the last successful action, for a status line ("Archived Acme."). */
  notice: string | null;
  /** Render this once next to the screen's content: it holds the dialogs. */
  dialogs: ReactNode;
}

/**
 * The create/edit dialog and the archive/restore confirmations for clients, shared by the list
 * and the detail page. `onChanged` runs after every successful change (reload your data); the
 * header switcher is refreshed for you.
 */
export function useClientActions(
  onChanged: (client: Client) => void,
): ClientActions {
  const [pending, setPending] = useState<Pending>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const access = useAccess();
  const selection = useOptionalSelection();
  const refreshSelection = selection?.refresh;

  function done(message: string, client: Client) {
    setPending(null);
    setNotice(message);
    refreshSelection?.();
    onChanged(client);
  }

  async function confirm(kind: "archive" | "restore", client: Client) {
    try {
      const updated =
        kind === "archive"
          ? await archiveClient(client.id)
          : await restoreClient(client.id);
      done(
        `${kind === "archive" ? "Archived" : "Restored"} ${client.name}.`,
        updated,
      );
    } catch (error) {
      if (access.noteError(error, "manage", client.id)) {
        throw new Error("You do not have permission to do this.");
      }
      throw error; // ConfirmDialog shows the message and stays open
    }
  }

  const dialogs = (
    <>
      <ClientFormDialog
        open={pending?.kind === "form"}
        client={pending?.kind === "form" ? pending.client : null}
        onClose={() => setPending(null)}
        onSaved={(saved) =>
          done(
            pending?.kind === "form" && pending.client
              ? `Saved ${saved.name}.`
              : `Created ${saved.name}.`,
            saved,
          )
        }
      />
      <ConfirmDialog
        open={pending?.kind === "archive"}
        title={`Archive ${pending?.client?.name ?? "client"}?`}
        description="It leaves the default lists and becomes read-only. Its campaigns and data are kept, and an admin can restore it later."
        confirmLabel="Archive client"
        onConfirm={() =>
          pending?.kind === "archive"
            ? confirm("archive", pending.client)
            : Promise.resolve()
        }
        onCancel={() => setPending(null)}
      />
      <ConfirmDialog
        open={pending?.kind === "restore"}
        title={`Restore ${pending?.client?.name ?? "client"}?`}
        description="It returns to the default lists and can be edited again."
        confirmLabel="Restore client"
        tone="primary"
        onConfirm={() =>
          pending?.kind === "restore"
            ? confirm("restore", pending.client)
            : Promise.resolve()
        }
        onCancel={() => setPending(null)}
      />
    </>
  );

  return {
    create: () => {
      setNotice(null);
      setPending({ kind: "form", client: null });
    },
    edit: (client) => {
      setNotice(null);
      setPending({ kind: "form", client });
    },
    archive: (client) => {
      setNotice(null);
      setPending({ kind: "archive", client });
    },
    restore: (client) => {
      setNotice(null);
      setPending({ kind: "restore", client });
    },
    notice,
    dialogs,
  };
}
