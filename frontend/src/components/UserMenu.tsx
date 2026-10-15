"use client";

import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { useOptionalAuth } from "@/lib/auth/AuthProvider";

/** The signed-in user and a sign-out control. Renders nothing outside an auth session. */
export function UserMenu() {
  const auth = useOptionalAuth();
  const [pending, setPending] = useState(false);
  if (!auth || auth.status !== "authenticated" || !auth.user) return null;
  const { user, logout } = auth;

  return (
    <div className="flex items-center gap-2">
      <Link
        href="/account"
        className="hidden max-w-48 truncate text-sm text-fg-muted hover:text-fg lg:inline"
        title="Your account and password"
        data-testid="current-user"
      >
        {user.name || user.email}
      </Link>
      <Button
        variant="ghost"
        size="sm"
        disabled={pending}
        onClick={async () => {
          setPending(true);
          await logout();
        }}
      >
        {pending ? "Signing out…" : "Sign out"}
      </Button>
    </div>
  );
}
