import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { THEME_INIT_SCRIPT, THEME_STORAGE_KEY } from "@/lib/theme";

import { ThemeToggle } from "./ThemeToggle";

afterEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

describe("ThemeToggle", () => {
  it("cycles system, light, dark and persists the choice", async () => {
    render(<ThemeToggle />);
    const button = screen.getByRole("button", { name: /theme: system/i });

    await userEvent.click(button);
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");

    await userEvent.click(
      screen.getByRole("button", { name: /theme: light/i }),
    );
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");

    await userEvent.click(screen.getByRole("button", { name: /theme: dark/i }));
    expect(document.documentElement).not.toHaveAttribute("data-theme");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
    expect(
      screen.getByRole("button", { name: /theme: system/i }),
    ).toBeInTheDocument();
  });

  it("restores a stored preference after mount", async () => {
    localStorage.setItem(THEME_STORAGE_KEY, "dark");
    render(<ThemeToggle />);
    expect(
      await screen.findByRole("button", { name: /theme: dark/i }),
    ).toBeInTheDocument();
  });

  it("init script applies a stored theme before hydration", () => {
    localStorage.setItem(THEME_STORAGE_KEY, "dark");
    new Function(THEME_INIT_SCRIPT)();
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
  });
});
