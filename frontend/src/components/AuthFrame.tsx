"use client";

import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect } from "react";

import { AppShell } from "@/components/AppShell";
import { AccessProvider } from "@/features/access/AccessProvider";
import { SelectionProvider } from "@/features/selection/SelectionProvider";
import { useAuth } from "@/lib/auth/AuthProvider";
import { LOGIN_PATH } from "@/lib/auth/constants";
import { isPublicPath, loginUrl } from "@/lib/auth/redirect";
import { MAIN_CONTENT_ID } from "@/lib/ids";

/** The chrome around a page: bare for public pages (login), guarded app shell otherwise. */
export function AuthFrame({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  if (isPublicPath(pathname)) {
    return (
      <main
        id={MAIN_CONTENT_ID}
        tabIndex={-1}
        className="flex min-h-screen items-center justify-center p-gutter outline-none"
      >
        {children}
      </main>
    );
  }
  return (
    <AuthGate>
      <AccessProvider>
        <SelectionProvider>
          <AppShell>{children}</AppShell>
        </SelectionProvider>
      </AccessProvider>
    </AuthGate>
  );
}

/**
 * Renders the app only for a signed-in user. When the session ends it sends the user to the login
 * page, remembering the current page (path, query and hash) so signing in returns them to it.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const { status, sessionEnd } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (status !== "unauthenticated") return;
    if (sessionEnd === "signed_out") {
      router.replace(LOGIN_PATH);
      return;
    }
    const here = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    router.replace(loginUrl(here, { expired: sessionEnd === "expired" }));
  }, [status, sessionEnd, router]);

  if (status === "authenticated") return <>{children}</>;
  return (
    <main
      id={MAIN_CONTENT_ID}
      tabIndex={-1}
      className="flex min-h-screen items-center justify-center p-gutter outline-none"
    >
      <p role="status" className="text-sm text-fg-muted">
        {status === "loading"
          ? "Loading your session…"
          : "Redirecting to sign in…"}
      </p>
    </main>
  );
}
