"use client";

import { useEffect, useRef } from "react";

export const UNSAVED_MESSAGE =
  "You have unsaved changes. Leave this page and discard them?";

/** True for a plain left click on a link that would navigate within this app. */
export function isInAppNavigation(
  event: MouseEvent,
  anchor: HTMLAnchorElement,
): boolean {
  if (event.defaultPrevented || event.button !== 0) return false;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
    return false;
  }
  if (anchor.target && anchor.target !== "_self") return false;
  if (anchor.hasAttribute("download")) return false;
  const url = new URL(anchor.href, window.location.href);
  if (url.origin !== window.location.origin) return false;
  // A hash link on this very page (such as the error summary's) is not leaving it.
  return !(
    url.pathname === window.location.pathname &&
    url.search === window.location.search
  );
}

/**
 * Warns before unsaved changes are lost:
 * - closing or reloading the tab (`beforeunload`, the browser's own prompt),
 * - clicking any in-app link (the App Router has no route-change events, so links are intercepted
 *   in the capture phase before Next's own handler sees the click),
 * - the browser's Back button (a sentinel history entry is pushed while dirty).
 * Confirmation uses `window.confirm`: it is accessible, blocking and needs no focus trap.
 * Returns `allowNavigation`, to call right before a navigation the form starts itself (after save).
 */
export function useUnsavedChangesGuard(
  dirty: boolean,
  message: string = UNSAVED_MESSAGE,
): { allowNavigation: () => void } {
  const active = useRef(dirty);
  const bypass = useRef(false);
  const sentinel = useRef(false);
  active.current = dirty;

  useEffect(() => {
    function onBeforeUnload(event: BeforeUnloadEvent) {
      if (!active.current || bypass.current) return;
      event.preventDefault();
      event.returnValue = ""; // required by some browsers to show the prompt
    }

    function onClick(event: MouseEvent) {
      if (!active.current || bypass.current) return;
      const anchor = (event.target as Element | null)?.closest?.("a[href]");
      if (!(anchor instanceof HTMLAnchorElement)) return;
      if (!isInAppNavigation(event, anchor)) return;
      if (!window.confirm(message)) {
        event.preventDefault();
        event.stopPropagation();
      }
    }

    function onPopState() {
      if (bypass.current || !sentinel.current) return;
      if (!active.current) {
        // Saved since the sentinel was pushed: nothing to protect, so Back should really go back.
        sentinel.current = false;
        window.history.back();
        return;
      }
      if (window.confirm(message)) {
        bypass.current = true;
        sentinel.current = false;
        window.history.back(); // past the entry this page pushed
      } else {
        window.history.pushState(
          window.history.state,
          "",
          window.location.href,
        );
      }
    }

    window.addEventListener("beforeunload", onBeforeUnload);
    document.addEventListener("click", onClick, true);
    window.addEventListener("popstate", onPopState);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
      document.removeEventListener("click", onClick, true);
      window.removeEventListener("popstate", onPopState);
    };
  }, [message]);

  // Push the sentinel entry once, the first time the form becomes dirty.
  useEffect(() => {
    if (dirty && !sentinel.current && !bypass.current) {
      window.history.pushState(window.history.state, "", window.location.href);
      sentinel.current = true;
    }
  }, [dirty]);

  return {
    allowNavigation: () => {
      bypass.current = true;
    },
  };
}
