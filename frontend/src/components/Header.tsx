import type { Ref } from "react";

import { SIDEBAR_ID } from "@/components/Sidebar";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Button } from "@/components/ui/Button";
import { UserMenu } from "@/components/UserMenu";

export function Header({
  menuOpen = false,
  onMenuClick,
  menuButtonRef,
}: {
  menuOpen?: boolean;
  onMenuClick?: () => void;
  menuButtonRef?: Ref<HTMLButtonElement>;
}) {
  return (
    <header className="flex h-header shrink-0 items-center justify-between gap-3 border-b border-line bg-surface-raised px-gutter md:px-6">
      <div className="flex items-center gap-3">
        <Button
          ref={menuButtonRef}
          variant="secondary"
          size="sm"
          className="md:hidden"
          aria-controls={SIDEBAR_ID}
          aria-expanded={menuOpen}
          onClick={onMenuClick}
        >
          Menu
        </Button>
        <span className="hidden text-sm text-fg-muted sm:inline">
          Account-based marketing
        </span>
      </div>
      <div className="flex items-center gap-2">
        <ThemeToggle />
        <UserMenu />
      </div>
    </header>
  );
}
