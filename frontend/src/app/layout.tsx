import "./globals.css";

import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AppShell, MAIN_CONTENT_ID } from "@/components/AppShell";
import { THEME_INIT_SCRIPT } from "@/lib/theme";

export const metadata: Metadata = {
  title: "ABM Engine",
  description: "Account-based marketing engine",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // suppressHydrationWarning: the init script may set data-theme before React hydrates.
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body>
        <a
          href={`#${MAIN_CONTENT_ID}`}
          className="sr-only z-50 rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-fg focus:not-sr-only focus:fixed focus:left-2 focus:top-2"
        >
          Skip to main content
        </a>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
