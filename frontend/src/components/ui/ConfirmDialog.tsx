"use client";

import { useEffect, useRef, useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description?: string;
  confirmLabel: string;
  /** `danger` for destructive actions (archive). */
  tone?: "danger" | "primary";
  /**
   * Runs when the user confirms. When it rejects, the dialog stays open and shows the error
   * message. After a success the caller closes the dialog (`open={false}`).
   */
  onConfirm: () => Promise<void>;
  onCancel: () => void;
}

/** A yes/no question in a modal. Focus starts on Cancel so Enter never destroys by accident. */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  tone = "danger",
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const cancel = useRef<HTMLButtonElement>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The dialog component stays mounted while closed: forget the last error.
  useEffect(() => {
    if (!open) setError(null);
  }, [open]);

  async function confirm() {
    setPending(true);
    setError(null);
    try {
      await onConfirm();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Something went wrong.",
      );
    } finally {
      setPending(false);
    }
  }

  function close() {
    if (pending) return;
    setError(null);
    onCancel();
  }

  return (
    <Modal
      open={open}
      onClose={close}
      title={title}
      description={description}
      initialFocusRef={cancel}
      footer={
        <>
          <Button
            ref={cancel}
            variant="secondary"
            disabled={pending}
            onClick={close}
          >
            Cancel
          </Button>
          <Button
            variant={tone === "danger" ? "danger" : "primary"}
            disabled={pending}
            onClick={() => void confirm()}
          >
            {pending ? "Working…" : confirmLabel}
          </Button>
        </>
      }
    >
      {error ? <Alert tone="danger">{error}</Alert> : null}
    </Modal>
  );
}
