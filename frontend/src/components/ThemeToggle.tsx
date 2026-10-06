"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { THEME_STORAGE_KEY, type ThemePreference } from "@/lib/theme";

const ORDER: ThemePreference[] = ["system", "light", "dark"];
const LABELS: Record<ThemePreference, string> = {
  system: "System",
  light: "Light",
  dark: "Dark",
};

function readStored(): ThemePreference {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // Storage can be blocked; fall back to following the system.
  }
  return "system";
}

function apply(theme: ThemePreference) {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
  try {
    if (theme === "system") localStorage.removeItem(THEME_STORAGE_KEY);
    else localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Ignore: the choice still applies for this page view.
  }
}

/** Cycles System -> Light -> Dark. The current choice is part of the label. */
export function ThemeToggle() {
  const [theme, setTheme] = useState<ThemePreference>("system");

  // Read the stored value after mount so server and client markup match.
  useEffect(() => {
    setTheme(readStored());
  }, []);

  function cycle() {
    const next = ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length] ?? "system";
    apply(next);
    setTheme(next);
  }

  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={cycle}
      aria-label={`Theme: ${LABELS[theme]}. Activate to change theme.`}
    >
      {LABELS[theme]}
    </Button>
  );
}
