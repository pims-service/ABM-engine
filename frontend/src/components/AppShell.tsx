"use client";

import { type KeyboardEvent, type ReactNode, useRef, useState } from "react";

import { Header } from "@/components/Header";
import { Sidebar } from "@/components/Sidebar";

export const MAIN_CONTENT_ID = "main-content";

/** Sidebar + header + content. Owns the small-screen drawer state. */
export function AppShell({ children }: { children: ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);

  function close({ restoreFocus }: { restoreFocus: boolean }) {
    setMenuOpen(false);
    if (restoreFocus) menuButton.current?.focus();
  }

  function onKeyDown(event: KeyboardEvent) {
    if (event.key === "Escape" && menuOpen) close({ restoreFocus: true });
  }

  return (
    <div className="flex h-screen" onKeyDown={onKeyDown}>
      <Sidebar
        open={menuOpen}
        onNavigate={() => close({ restoreFocus: false })}
      />
      {menuOpen ? (
        <div
          aria-hidden="true"
          className="fixed inset-0 z-20 bg-overlay md:hidden"
          onClick={() => close({ restoreFocus: false })}
        />
      ) : null}
      <div className="flex min-w-0 flex-1 flex-col">
        <Header
          menuOpen={menuOpen}
          menuButtonRef={menuButton}
          onMenuClick={() => setMenuOpen((open) => !open)}
        />
        <main
          id={MAIN_CONTENT_ID}
          tabIndex={-1}
          className="flex-1 overflow-y-auto p-gutter outline-none md:p-6"
        >
          {children}
        </main>
      </div>
    </div>
  );
}
