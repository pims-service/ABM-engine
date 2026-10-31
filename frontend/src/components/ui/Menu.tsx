"use client";

import Link from "next/link";
import { type ReactNode, useEffect, useRef, useState } from "react";

import { buttonStyles } from "@/components/ui/Button";
import { cn } from "@/lib/cn";

export interface MenuItem {
  label: string;
  description?: string;
  /** A link when `href` is set, otherwise a button that runs `onSelect`. */
  href?: string;
  onSelect?: () => void;
}

export interface MenuGroup {
  heading?: string;
  items: MenuItem[];
}

const ITEM =
  "block w-full rounded-md px-3 py-2 text-left text-sm hover:bg-surface-muted focus-visible:bg-surface-muted";

/** A button that opens a small grouped list of actions; closes on pick, outside click or Escape. */
export function Menu({
  label,
  groups,
  ariaLabel,
  size,
  panelClassName = "w-72",
}: {
  label: ReactNode;
  groups: MenuGroup[];
  ariaLabel?: string;
  size?: "sm" | "md";
  panelClassName?: string;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onPointer);
    return () => document.removeEventListener("mousedown", onPointer);
  }, [open]);

  return (
    <div
      ref={root}
      className="relative"
      onKeyDown={(event) => {
        // Escape closes the menu only: stop it here so a dialog around the menu stays open.
        if (open && event.key === "Escape") {
          event.stopPropagation();
          setOpen(false);
        }
      }}
    >
      <button
        type="button"
        aria-label={ariaLabel}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className={buttonStyles({ variant: "secondary", size })}
      >
        {label}
      </button>
      {open ? (
        <div
          role="menu"
          className={cn(
            "absolute right-0 z-20 mt-2 rounded-lg border border-line bg-surface-raised p-2 shadow-lg",
            panelClassName,
          )}
        >
          {groups.map((group, index) => (
            <div
              key={group.heading ?? index}
              className={cn(index > 0 && "mt-2 border-t border-line pt-2")}
            >
              {group.heading ? (
                <p className="px-3 pb-1 pt-1 text-xs font-medium uppercase tracking-wide text-fg-muted">
                  {group.heading}
                </p>
              ) : null}
              {group.items.map((item) => {
                const body = (
                  <>
                    <span className="block font-medium">{item.label}</span>
                    {item.description ? (
                      <span className="block text-xs text-fg-muted">
                        {item.description}
                      </span>
                    ) : null}
                  </>
                );
                return item.href ? (
                  <Link
                    key={item.label}
                    href={item.href}
                    role="menuitem"
                    className={ITEM}
                    onClick={() => setOpen(false)}
                  >
                    {body}
                  </Link>
                ) : (
                  <button
                    key={item.label}
                    type="button"
                    role="menuitem"
                    className={ITEM}
                    onClick={() => {
                      setOpen(false);
                      item.onSelect?.();
                    }}
                  >
                    {body}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
