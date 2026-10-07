"use client";

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  ApiError,
  setAccessTokenGetter,
  setUnauthorizedHandler,
} from "@/lib/api";

import { authApi } from "./browser";
import type { AuthUser, SessionTokens } from "./types";

export type AuthStatus = "loading" | "authenticated" | "unauthenticated";

/** Why the session ended: the user signed out, or it expired / was revoked. */
export type SessionEnd = "signed_out" | "expired" | null;

export interface AuthContextValue {
  status: AuthStatus;
  user: AuthUser | null;
  sessionEnd: SessionEnd;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  /** Get a fresh access token now (shared by concurrent callers); null when the session is gone. */
  refresh: () => Promise<string | null>;
}

/** Refresh this long before the access token expires. */
export const REFRESH_SKEW_MS = 60_000;
/** Retry delay after a refresh failed for a reason other than "session invalid". */
export const REFRESH_RETRY_MS = 15_000;
/** Gap before re-checking a 401: another tab may have just rotated the cookie. */
export const REFRESH_RECHECK_MS = 400;
const MAX_TIMER_MS = 2 ** 31 - 1;

interface RefreshOptions {
  /** Ask again once on a 401 (another tab may have rotated the cookie). */
  recheck?: boolean;
  /** First load: having no session is the normal outcome, not an expiry. */
  initial?: boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>.");
  return value;
}

/** Like {@link useAuth} but returns null outside a provider (used by optional UI such as the header). */
export function useOptionalAuth(): AuthContextValue | null {
  return useContext(AuthContext);
}

function isSessionGone(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Owns the session. The access token lives only in a ref (memory), never in storage; the refresh
 * token is an httpOnly cookie managed by the BFF. A timer refreshes the access token shortly
 * before it expires, and requests that still get a 401 trigger one refresh and a retry.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<AuthUser | null>(null);
  const [sessionEnd, setSessionEnd] = useState<SessionEnd>(null);

  const tokenRef = useRef<SessionTokens | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const inFlightRef = useRef<Promise<string | null> | null>(null);
  const refreshRef = useRef<
    (options?: RefreshOptions) => Promise<string | null>
  >(async () => null);

  const clearTimer = useCallback(() => {
    clearTimeout(timerRef.current);
    timerRef.current = undefined;
  }, []);

  const endSession = useCallback(
    (reason: SessionEnd) => {
      clearTimer();
      tokenRef.current = null;
      setUser(null);
      setSessionEnd(reason);
      setStatus("unauthenticated");
    },
    [clearTimer],
  );

  const schedule = useCallback(
    (tokens: SessionTokens) => {
      clearTimer();
      const remaining = tokens.expires_at * 1000 - Date.now();
      const wait =
        remaining > 2 * REFRESH_SKEW_MS
          ? remaining - REFRESH_SKEW_MS
          : Math.max(remaining / 2, 0);
      timerRef.current = setTimeout(
        () => void refreshRef.current(),
        Math.min(wait, MAX_TIMER_MS),
      );
    },
    [clearTimer],
  );

  const accept = useCallback(
    (tokens: SessionTokens) => {
      tokenRef.current = tokens;
      schedule(tokens);
    },
    [schedule],
  );

  const refresh = useCallback(
    (options: RefreshOptions = {}): Promise<string | null> => {
      if (inFlightRef.current) return inFlightRef.current;
      const run = (async () => {
        try {
          let tokens: SessionTokens;
          try {
            tokens = await authApi.refresh();
          } catch (error) {
            if (!isSessionGone(error) || !options.recheck) throw error;
            // Refresh tokens rotate: if another tab refreshed a moment ago our cookie is already
            // the new one, so ask once more before treating the session as lost.
            await delay(REFRESH_RECHECK_MS);
            tokens = await authApi.refresh();
          }
          accept(tokens);
          return tokens.access;
        } catch (error) {
          if (isSessionGone(error)) {
            endSession(options.initial ? null : "expired");
          } else if (tokenRef.current) {
            // API or network trouble: keep the session and try again soon.
            clearTimer();
            timerRef.current = setTimeout(
              () => void refreshRef.current({ recheck: true }),
              REFRESH_RETRY_MS,
            );
          }
          return null;
        } finally {
          inFlightRef.current = null;
        }
      })();
      inFlightRef.current = run;
      return run;
    },
    [accept, clearTimer, endSession],
  );
  refreshRef.current = (options) => refresh({ recheck: true, ...options });

  // Restore the session on first load (the access token is not persisted anywhere).
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const access = await refresh({ initial: true });
      if (cancelled) return;
      if (!access) {
        // endSession already ran for a rejected cookie; this covers "no cookie / API down".
        setStatus((current) =>
          current === "loading" ? "unauthenticated" : current,
        );
        return;
      }
      try {
        const me = await authApi.me(access);
        if (cancelled) return;
        setUser(me);
        setSessionEnd(null);
        setStatus("authenticated");
      } catch (error) {
        if (cancelled) return;
        if (isSessionGone(error)) endSession("expired");
        else setStatus("unauthenticated");
      }
    })();
    return () => {
      cancelled = true;
      clearTimer();
    };
  }, [refresh, endSession, clearTimer]);

  // Wire the typed API client to this session.
  useEffect(() => {
    setAccessTokenGetter(async () => {
      const tokens = tokenRef.current;
      if (!tokens) return null;
      // A sleeping tab can miss its timer: top up here if the token is (nearly) expired.
      if (tokens.expires_at * 1000 - Date.now() < 10_000) {
        return (await refreshRef.current()) ?? null;
      }
      return tokens.access;
    });
    setUnauthorizedHandler(() => refreshRef.current());
    return () => {
      setAccessTokenGetter(undefined);
      setUnauthorizedHandler(undefined);
    };
  }, []);

  // Coming back to a tab after a long time: refresh if the token ran out meanwhile.
  useEffect(() => {
    function onVisible() {
      const tokens = tokenRef.current;
      if (
        document.visibilityState === "visible" &&
        tokens &&
        tokens.expires_at * 1000 - Date.now() < REFRESH_SKEW_MS
      ) {
        void refreshRef.current();
      }
    }
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      const tokens = await authApi.login(email, password);
      const me = await authApi.me(tokens.access);
      accept(tokens);
      setUser(me);
      setSessionEnd(null);
      setStatus("authenticated");
    },
    [accept],
  );

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } catch {
      // The cookie is cleared server side whenever the request arrives; sign out locally anyway.
    }
    endSession("signed_out");
  }, [endSession]);

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      user,
      sessionEnd,
      login,
      logout,
      refresh: () => refreshRef.current(),
    }),
    [status, user, sessionEnd, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
