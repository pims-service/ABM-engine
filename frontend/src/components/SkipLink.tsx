"use client";

import type { MouseEvent } from "react";

import { MAIN_CONTENT_ID } from "@/lib/ids";

/**
 * First tab stop. Moves focus (not just scroll position) to the main landmark,
 * because browsers do not reliably focus a fragment target on their own.
 */
export function SkipLink() {
  function onClick(event: MouseEvent<HTMLAnchorElement>) {
    const main = document.getElementById(MAIN_CONTENT_ID);
    if (!main) return; // fall back to the native anchor behaviour
    event.preventDefault();
    main.focus();
    main.scrollIntoView();
  }

  return (
    <a
      href={`#${MAIN_CONTENT_ID}`}
      onClick={onClick}
      className="sr-only z-50 rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-fg focus:not-sr-only focus:fixed focus:left-2 focus:top-2"
    >
      Skip to main content
    </a>
  );
}
