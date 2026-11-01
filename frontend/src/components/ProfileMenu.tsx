"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";

import { useOptionalAuth } from "@/lib/auth/AuthProvider";

/** Up to two letters for the avatar: the first letters of the name, or of the email's start. */
function initialsOf(name: string, email: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length > 1) return (words[0]![0]! + words[1]![0]!).toUpperCase();
  return (words[0] ?? email.split("@")[0] ?? "?").slice(0, 2).toUpperCase();
}

const LINK =
  "block rounded-md px-3 py-2 text-sm hover:bg-surface-muted focus-visible:bg-surface-muted";

/**
 * The signed-in user as a round avatar. It opens a panel with who you are, the
 * settings pages and Sign out. Renders nothing outside an auth session.
 */
export function ProfileMenu() {
  const auth = useOptionalAuth();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onPointer);
    return () => document.removeEventListener("mousedown", onPointer);
  }, [open]);

  if (!auth || auth.status !== "authenticated" || !auth.user) return null;
  const { user, logout } = auth;
  const label = user.name || user.email;
  const close = () => setOpen(false);

  return (
    <div
      ref={root}
      className="relative"
      onKeyDown={(event) => {
        // Escape closes the panel only, wherever the focus is inside it.
        if (open && event.key === "Escape") {
          event.stopPropagation();
          setOpen(false);
        }
      }}
    >
      <button
        type="button"
        aria-label="Your profile"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen(!open)}
        className="grid h-10 w-10 place-items-center rounded-full border-2 border-line-strong bg-fg text-sm font-bold text-surface transition hover:scale-105 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-fg"
      >
        <span aria-hidden="true">{initialsOf(user.name, user.email)}</span>
        {/* Always in the page, so the signed-in name stays findable by assistive tech and tests. */}
        <span className="sr-only" data-testid="current-user">
          {label}
        </span>
      </button>
      {open ? (
        <div
          id={panelId}
          className="absolute right-0 z-30 mt-2 w-72 rounded-lg border border-line bg-surface-raised p-2 shadow-lg"
        >
          <div className="flex items-center gap-3 px-3 py-2">
            <span
              aria-hidden="true"
              className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-fg text-sm font-bold text-surface"
            >
              {initialsOf(user.name, user.email)}
            </span>
            <div className="min-w-0">
              {user.name ? (
                <p className="truncate text-sm font-medium">{user.name}</p>
              ) : null}
              <p className="truncate text-xs text-fg-muted">{user.email}</p>
            </div>
          </div>

          <div className="mt-1 border-t border-line pt-1">
            <Link href="/account" className={LINK} onClick={close}>
              Your account
            </Link>
            <Link
              href="/settings/integrations"
              className={LINK}
              onClick={close}
            >
              Integrations
            </Link>
            <Link href="/settings/usage" className={LINK} onClick={close}>
              AI usage and limits
            </Link>
          </div>

          <div className="border-t border-line pt-1">
            <button
              type="button"
              disabled={pending}
              className={`${LINK} w-full text-left disabled:opacity-60`}
              onClick={async () => {
                setPending(true);
                await logout();
              }}
            >
              {pending ? "Signing out…" : "Sign out"}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
