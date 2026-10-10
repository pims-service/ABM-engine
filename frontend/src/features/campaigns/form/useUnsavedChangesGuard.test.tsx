import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useUnsavedChangesGuard } from "./useUnsavedChangesGuard";

function Harness({ dirty }: { dirty: boolean }) {
  useUnsavedChangesGuard(dirty, "Discard?");
  return (
    <>
      <a href="/campaigns">Campaigns</a>
      <a href="#f-offer">Offer</a>
      <a href="https://example.org/">External</a>
    </>
  );
}

afterEach(() => vi.restoreAllMocks());

describe("useUnsavedChangesGuard", () => {
  it("asks before the tab closes only while dirty", () => {
    const { rerender } = render(<Harness dirty={false} />);
    const clean = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);

    rerender(<Harness dirty />);
    const dirty = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(dirty);
    expect(dirty.defaultPrevented).toBe(true);
  });

  it("cancels an in-app link click when the person declines", () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<Harness dirty />);
    const link = screen.getByRole("link", { name: "Campaigns" });
    const notCancelled = fireEvent.click(link);
    expect(confirm).toHaveBeenCalledWith("Discard?");
    expect(notCancelled).toBe(false);
  });

  it("lets the click through when the person agrees", () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<Harness dirty />);
    const link = screen.getByRole("link", { name: "Campaigns" });
    link.addEventListener("click", (e) => e.preventDefault()); // jsdom cannot navigate
    fireEvent.click(link);
    expect(window.confirm).toHaveBeenCalledOnce();
  });

  it("ignores clean forms, same-page hash links, external links and modified clicks", () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { rerender } = render(<Harness dirty={false} />);
    fireEvent.click(screen.getByRole("link", { name: "Campaigns" }));
    expect(confirm).not.toHaveBeenCalled();

    rerender(<Harness dirty />);
    for (const name of ["Offer", "External"]) {
      const link = screen.getByRole("link", { name });
      link.addEventListener("click", (e) => e.preventDefault());
      fireEvent.click(link);
    }
    const link = screen.getByRole("link", { name: "Campaigns" });
    link.addEventListener("click", (e) => e.preventDefault());
    fireEvent.click(link, { ctrlKey: true });
    expect(confirm).not.toHaveBeenCalled();
  });
});
