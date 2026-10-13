"use client";

import { type ReactNode, useState } from "react";

import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useAccess } from "@/features/access/AccessProvider";
import { ApiError } from "@/lib/api";
import { errorMessage } from "@/lib/format";

import {
  type Connection,
  type ConnectionCategory,
  type ConnectionTestResult,
  type ConnectionTransition,
  testConnection,
  transitionConnection,
  updateConnection,
} from "./api";
import { ConnectionFormDialog } from "./ConnectionFormDialog";
import { CredentialsDialog } from "./CredentialsDialog";
import { NO_PERMISSION } from "./formErrors";

type Pending =
  | {
      kind: "form";
      connection: Connection | null;
      category?: ConnectionCategory;
    }
  | { kind: "credentials"; connection: Connection }
  | { kind: "clear"; connection: Connection }
  | { kind: ConnectionTransition; connection: Connection }
  | null;

/** What the row shows about a test run. */
export type TestState =
  | { state: "running" }
  | { state: "done"; result: ConnectionTestResult }
  | { state: "error"; message: string };

export interface ConnectionActions {
  create: (category?: ConnectionCategory) => void;
  edit: (connection: Connection) => void;
  credentials: (connection: Connection) => void;
  /** Ask for confirmation, then enable, disable, archive or restore. */
  transition: (kind: ConnectionTransition, connection: Connection) => void;
  /** Run the connection test; progress and result are in `tests[connection.id]`. */
  test: (connection: Connection) => void;
  tests: Record<string, TestState>;
  notice: string | null;
  /** Render once next to the screen's content: it holds the dialogs. */
  dialogs: ReactNode;
}

const TRANSITION_COPY: Record<
  ConnectionTransition,
  {
    title: (name: string) => string;
    description: string;
    confirm: string;
    done: string;
    tone: "danger" | "primary";
  }
> = {
  enable: {
    title: (name) => `Enable ${name}?`,
    description:
      "Adapters can use it again. A connection in the error state returns to active.",
    confirm: "Enable connection",
    done: "Enabled",
    tone: "primary",
  },
  disable: {
    title: (name) => `Disable ${name}?`,
    description:
      "Adapters stop using it. Its settings and credentials are kept, and you can enable it again.",
    confirm: "Disable connection",
    done: "Disabled",
    tone: "danger",
  },
  archive: {
    title: (name) => `Archive ${name}?`,
    description:
      "It leaves the default list and adapters stop using it. Nothing is deleted, and an admin can restore it.",
    confirm: "Archive connection",
    done: "Archived",
    tone: "danger",
  },
  restore: {
    title: (name) => `Restore ${name}?`,
    description:
      "It returns to the list and can be edited again. Restoring fails if another active connection now has the same name.",
    confirm: "Restore connection",
    done: "Restored",
    tone: "primary",
  },
};

function testErrorMessage(error: ApiError | Error): string {
  if (error instanceof ApiError && error.code === "throttled") {
    const wait = error.retryAfter;
    return `Too many tests. Try again${wait ? ` in ${wait} seconds` : " in a minute"}.`;
  }
  return errorMessage(error);
}

/**
 * Add/edit, credentials, enable/disable/archive/restore and the connection test, for one
 * client's integrations page. `onChanged` runs after every successful change (reload the list).
 */
export function useConnectionActions(
  clientId: string,
  onChanged: () => void,
): ConnectionActions {
  const [pending, setPending] = useState<Pending>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [tests, setTests] = useState<Record<string, TestState>>({});
  const access = useAccess();

  function done(message: string) {
    setPending(null);
    setNotice(message);
    onChanged();
  }

  async function runTransition(
    kind: ConnectionTransition,
    connection: Connection,
  ) {
    try {
      await transitionConnection(connection.id, kind);
      done(`${TRANSITION_COPY[kind].done} ${connection.name}.`);
    } catch (error) {
      if (access.noteError(error, "manage", clientId)) {
        throw new Error(NO_PERMISSION);
      }
      throw error; // ConfirmDialog shows the message and stays open
    }
  }

  async function runClear(connection: Connection) {
    try {
      await updateConnection(connection.id, { credentials: null });
      done(`Cleared the credentials of ${connection.name}.`);
    } catch (error) {
      if (access.noteError(error, "manage", clientId)) {
        throw new Error(NO_PERMISSION);
      }
      throw error;
    }
  }

  function test(connection: Connection) {
    setNotice(null);
    setTests((current) => ({
      ...current,
      [connection.id]: { state: "running" },
    }));
    testConnection(connection.id)
      .then((result) => {
        setTests((current) => ({
          ...current,
          [connection.id]: { state: "done", result },
        }));
        onChanged();
      })
      .catch((error: unknown) => {
        const denied = access.noteError(error, "manage", clientId);
        setTests((current) => ({
          ...current,
          [connection.id]: {
            state: "error",
            message: denied ? NO_PERMISSION : testErrorMessage(error as Error),
          },
        }));
      });
  }

  const transition =
    pending && pending.kind in TRANSITION_COPY
      ? (pending as { kind: ConnectionTransition; connection: Connection })
      : null;
  const copy = transition ? TRANSITION_COPY[transition.kind] : null;

  const dialogs = (
    <>
      <ConnectionFormDialog
        open={pending?.kind === "form"}
        clientId={clientId}
        connection={pending?.kind === "form" ? pending.connection : null}
        defaultCategory={
          pending?.kind === "form" ? pending.category : undefined
        }
        onClose={() => setPending(null)}
        onSaved={(saved) =>
          done(
            pending?.kind === "form" && pending.connection
              ? `Saved ${saved.name}.`
              : `Added ${saved.name}.`,
          )
        }
      />
      <CredentialsDialog
        open={pending?.kind === "credentials"}
        connection={pending?.kind === "credentials" ? pending.connection : null}
        onClose={() => setPending(null)}
        onSaved={(saved) => done(`Saved the credentials of ${saved.name}.`)}
        onRequestClear={(connection) =>
          setPending({ kind: "clear", connection })
        }
      />
      <ConfirmDialog
        open={pending?.kind === "clear"}
        title={`Clear credentials of ${pending?.connection?.name ?? "connection"}?`}
        description="The stored secrets are deleted and cannot be recovered. The connection cannot be tested or used until you set new credentials."
        confirmLabel="Clear credentials"
        onConfirm={() =>
          pending?.kind === "clear"
            ? runClear(pending.connection)
            : Promise.resolve()
        }
        onCancel={() => setPending(null)}
      />
      <ConfirmDialog
        open={transition !== null}
        title={
          transition && copy
            ? copy.title(transition.connection.name)
            : "Confirm"
        }
        description={copy?.description}
        confirmLabel={copy?.confirm ?? "Confirm"}
        tone={copy?.tone}
        onConfirm={() =>
          transition
            ? runTransition(transition.kind, transition.connection)
            : Promise.resolve()
        }
        onCancel={() => setPending(null)}
      />
    </>
  );

  return {
    create: (category) => {
      setNotice(null);
      setPending({ kind: "form", connection: null, category });
    },
    edit: (connection) => {
      setNotice(null);
      setPending({ kind: "form", connection });
    },
    credentials: (connection) => {
      setNotice(null);
      setPending({ kind: "credentials", connection });
    },
    transition: (kind, connection) => {
      setNotice(null);
      setPending({ kind, connection });
    },
    test,
    tests,
    notice,
    dialogs,
  };
}
