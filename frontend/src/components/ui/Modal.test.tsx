import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { ConfirmDialog } from "./ConfirmDialog";
import { Modal } from "./Modal";

function Harness({ onClose = () => {} }: { onClose?: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>Open</button>
      <Modal
        open={open}
        title="Edit thing"
        description="Change it."
        onClose={() => {
          onClose();
          setOpen(false);
        }}
      >
        <input aria-label="First" />
        <input aria-label="Second" />
      </Modal>
    </>
  );
}

describe("Modal", () => {
  it("renders nothing while closed", () => {
    render(<Modal open={false} title="Hidden" onClose={() => {}} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("is a labelled, described modal dialog and moves focus inside", async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "Open" }));
    const dialog = screen.getByRole("dialog", { name: "Edit thing" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAccessibleDescription("Change it.");
    expect(screen.getByRole("textbox", { name: "First" })).toHaveFocus();
  });

  it("keeps Tab inside the dialog in both directions", async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "Open" }));
    await userEvent.tab();
    expect(screen.getByRole("textbox", { name: "Second" })).toHaveFocus();
    await userEvent.tab();
    expect(screen.getByRole("textbox", { name: "First" })).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(screen.getByRole("textbox", { name: "Second" })).toHaveFocus();
  });

  it("closes on Escape and returns focus to the opener", async () => {
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    const opener = screen.getByRole("button", { name: "Open" });
    await userEvent.click(opener);
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(opener).toHaveFocus());
  });

  it("closes when the backdrop is pressed but not when the dialog is", async () => {
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    await userEvent.click(screen.getByRole("button", { name: "Open" }));
    await userEvent.click(screen.getByRole("dialog"));
    expect(onClose).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("dialog").parentElement as Element);
    expect(onClose).toHaveBeenCalledOnce();
  });
});

describe("ConfirmDialog", () => {
  function setup(onConfirm: () => Promise<void>) {
    const onCancel = vi.fn();
    render(
      <ConfirmDialog
        open
        title="Archive Acme?"
        description="It becomes read-only."
        confirmLabel="Archive client"
        onConfirm={onConfirm}
        onCancel={onCancel}
      />,
    );
    return { onCancel };
  }

  it("focuses Cancel first so Enter does not destroy anything", () => {
    setup(() => Promise.resolve());
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
  });

  it("calls onConfirm when confirmed and onCancel when cancelled", async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    const { onCancel } = setup(onConfirm);
    await userEvent.click(
      screen.getByRole("button", { name: "Archive client" }),
    );
    expect(onConfirm).toHaveBeenCalledOnce();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("shows the error and stays open when the action fails", async () => {
    setup(() => Promise.reject(new Error("Client has active jobs.")));
    await userEvent.click(
      screen.getByRole("button", { name: "Archive client" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Client has active jobs.",
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Archive client" }),
    ).toBeEnabled();
  });

  it("disables the buttons while the action runs", async () => {
    let finish: () => void = () => {};
    setup(() => new Promise<void>((resolve) => (finish = resolve)));
    await userEvent.click(
      screen.getByRole("button", { name: "Archive client" }),
    );
    expect(screen.getByRole("button", { name: "Working…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    finish();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Archive client" }),
      ).toBeEnabled(),
    );
  });
});
