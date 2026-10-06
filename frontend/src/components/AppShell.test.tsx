import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AppShell } from "./AppShell";

vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));

describe("AppShell", () => {
  it("renders navigation and a focusable main landmark for the skip link", () => {
    render(
      <AppShell>
        <h1>Hello</h1>
      </AppShell>,
    );
    expect(screen.getByRole("navigation", { name: "Main" })).toBeVisible();
    const main = screen.getByRole("main");
    expect(main).toHaveAttribute("id", "main-content");
    expect(main).toHaveAttribute("tabindex", "-1");
    expect(screen.getByRole("heading", { name: "Hello" })).toBeInTheDocument();
  });

  it("toggles the mobile menu and closes it with Escape, restoring focus", async () => {
    render(<AppShell>content</AppShell>);
    const menu = screen.getByRole("button", { name: "Menu" });
    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(menu).toHaveAttribute("aria-controls", "app-sidebar");

    await userEvent.click(menu);
    expect(menu).toHaveAttribute("aria-expanded", "true");

    await userEvent.keyboard("{Escape}");
    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(menu).toHaveFocus();
  });

  it("closes the menu when a nav link is chosen", async () => {
    render(<AppShell>content</AppShell>);
    const menu = screen.getByRole("button", { name: "Menu" });
    await userEvent.click(menu);
    await userEvent.click(screen.getByRole("link", { name: "Campaigns" }));
    expect(menu).toHaveAttribute("aria-expanded", "false");
  });
});
