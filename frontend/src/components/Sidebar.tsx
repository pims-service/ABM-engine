"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useAccess } from "@/features/access/AccessProvider";
import { useOptionalSelection } from "@/features/selection/SelectionProvider";
import { cn } from "@/lib/cn";

const ICONS: Record<string, string> = {
  "/dashboard": "M3 12l9-8 9 8M5 10v10h5v-6h4v6h5V10",
  "/clients": "M16 11a4 4 0 10-8 0 4 4 0 008 0zM4 20a8 8 0 0116 0",
  "/campaigns": "M4 5h16M4 12h16M4 19h10",
  "/companies": "M4 20V6l8-3v17M12 9h8v11M8 9h1M8 13h1M8 17h1M16 13h1M16 17h1",
  "/settings/integrations": "M9 3v4M15 3v4M7 7h10v5a5 5 0 01-10 0V7zM12 17v4",
};

function NavIcon({ href }: { href: string }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className="size-4 shrink-0"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={ICONS[href] ?? ""} />
    </svg>
  );
}

const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/clients", label: "Clients" },
  { href: "/campaigns", label: "Campaigns" },
  { href: "/companies", label: "Companies" },
  { href: "/settings/integrations", label: "Integrations" },
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
  const access = useAccess();
  const selectedClient = useOptionalSelection()?.clientId ?? undefined;
  // Integrations need the manager level in the selected client. The link stays while the role is
  // unknown (the page explains a refusal) and goes once the API has said 403.
  const items = NAV_ITEMS.filter(
    (item) =>
      item.href !== "/settings/integrations" ||
      access.can("edit", selectedClient),
  );

  return (
    <aside
      id={SIDEBAR_ID}
      className={cn(
        "z-30 flex w-sidebar shrink-0 flex-col border-r border-line bg-surface transition-transform",
        "max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:shadow-md",
        open
          ? "max-md:translate-x-0"
          : "max-md:invisible max-md:-translate-x-full",
      )}
    >
      <div className="flex h-header items-center gap-2.5 px-5 font-semibold tracking-tight">
        <span
          aria-hidden="true"
          className="grid size-6 place-items-center rounded-md bg-accent text-xs font-bold text-accent-fg"
        >
          A
        </span>
        <span>ABM Engine</span>
      </div>
      <nav aria-label="Main" className="flex flex-col gap-0.5 px-3 py-2">
        {items.map(({ href, label }) => {
          const active = pathname === href || pathname.startsWith(`${href}/`);
          return (
            <Link
              key={href}
              href={href}
              onClick={onNavigate}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm transition-colors",
                active
                  ? "bg-surface-muted font-medium text-fg"
                  : "text-fg-muted hover:bg-surface-muted hover:text-fg",
              )}
            >
              <NavIcon href={href} />
              {label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
