"use client";

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";

import { ApiError } from "@/lib/api";

/**
 * What a user may do, as far as the UI knows. `edit` is the EDIT level (manager, admin: edit
 * clients and campaigns, clone, activate), `manage` is MANAGE (admin: archive, restore, create
 * clients). See docs/permissions.md.
 *
 * KNOWN GAP: `GET /api/v1/auth/me/` does not expose roles or memberships, and the client and
 * campaign payloads carry no "what can I do here" flags. So the UI starts optimistic (every action
 * is shown) and learns from the API: the first 403 for a level hides that level's actions for that
 * client from then on (this session). The API stays the only real gate. Once the API exposes the
 * role per client, feed it into `AccessProvider` (the `initialDenied` prop is the seam) and the
 * viewer/reviewer UI will be correct from the first render.
 */
export type AccessLevel = "edit" | "manage";

/** Scope for actions that belong to no client yet (creating a client). */
export const PLATFORM_SCOPE = "*";

export interface AccessContextValue {
  /** May the user do `level` things in `clientId` (default: platform scope)? */
  can: (level: AccessLevel, clientId?: string) => boolean;
  /**
   * Call with any error from an action. A 403 `ApiError` marks `level` (and, for `edit`, also
   * `manage`) as denied for `clientId`, which hides those actions. Returns true when it was a 403.
   */
  noteError: (error: unknown, level: AccessLevel, clientId?: string) => boolean;
}

const PERMISSIVE: AccessContextValue = {
  can: () => true,
  noteError: (error) => error instanceof ApiError && error.status === 403,
};

const AccessContext = createContext<AccessContextValue>(PERMISSIVE);

/** Access decisions for the UI. Outside an `AccessProvider` everything is allowed (API decides). */
export function useAccess(): AccessContextValue {
  return useContext(AccessContext);
}

const key = (level: AccessLevel, clientId: string) => `${level}:${clientId}`;

export function AccessProvider({
  children,
  initialDenied = [],
}: {
  children: ReactNode;
  /** Pre-denied entries, for when the roles become available from the API (and for tests). */
  initialDenied?: ReadonlyArray<{ level: AccessLevel; clientId?: string }>;
}) {
  const [denied, setDenied] = useState<ReadonlySet<string>>(() => {
    const set = new Set<string>();
    for (const { level, clientId = PLATFORM_SCOPE } of initialDenied) {
      set.add(key(level, clientId));
      // Roles are cumulative: no EDIT means no MANAGE either.
      if (level === "edit") set.add(key("manage", clientId));
    }
    return set;
  });

  const can = useCallback(
    (level: AccessLevel, clientId: string = PLATFORM_SCOPE) =>
      !denied.has(key(level, clientId)),
    [denied],
  );

  const noteError = useCallback(
    (error: unknown, level: AccessLevel, clientId: string = PLATFORM_SCOPE) => {
      if (!(error instanceof ApiError) || error.status !== 403) return false;
      setDenied((current) => {
        const next = new Set(current);
        next.add(key(level, clientId));
        // Roles are cumulative: someone who cannot edit cannot manage either.
        if (level === "edit") next.add(key("manage", clientId));
        return next;
      });
      return true;
    },
    [],
  );

  const value = useMemo(() => ({ can, noteError }), [can, noteError]);
  return (
    <AccessContext.Provider value={value}>{children}</AccessContext.Provider>
  );
}
