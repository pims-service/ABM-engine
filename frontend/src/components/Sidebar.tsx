"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/cn";

const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/campaigns", label: "Campaigns" },
  { href: "/companies", label: "Companies" },
] as const;

export const SIDEBAR_ID = "app-sidebar";

/**
 * Persistent column from `md` up; below that it is an off-canvas drawer
 * controlled by `open`. While closed on small screens it is `invisible`, so its
 * links are not reachable by keyboard or screen reader.
 */
export function Sidebar({
  open = false,
  onNavigate,
}: {
  open?: boolean;
  /** Called when a nav link is activated (used to close the mobile drawer). */
  onNavigate?: () => void;
}) {
  const pathname = usePathname();

  return (
    <aside
      id={SIDEBAR_ID}
      className={cn(
        "z-30 flex w-sidebar shrink-0 flex-col border-r border-line bg-surface-raised transition-transform",
        "max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:shadow-md",
        open
          ? "max-md:translate-x-0"
          : "max-md:invisible max-md:-translate-x-full",
      )}
    >
      <div className="flex h-header items-center border-b border-line px-4 font-semibold">
        ABM Engine
      </div>
      <nav aria-label="Main" className="flex flex-col gap-1 p-3">
        {NAV_ITEMS.map(({ href, label }) => {
          const active = pathname === href || pathname.startsWith(`${href}/`);
          return (
            <Link
              key={href}
              href={href}
              onClick={onNavigate}
              aria-current={active ? "page" : undefined}
              className={cn(
                "rounded-md px-3 py-2 text-sm font-medium",
                active
                  ? "bg-accent text-accent-fg"
                  : "text-fg-muted hover:bg-surface-muted hover:text-fg",
              )}
            >
              {label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
