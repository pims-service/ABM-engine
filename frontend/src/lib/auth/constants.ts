/**
 * Shared by the middleware, route handlers and client code. Keep this file free of
 * "use client" / "server-only" so every side can import it.
 */

/**
 * httpOnly cookie that holds the API refresh token. The name differs from the backend's own
 * `AUTH_REFRESH_COOKIE_NAME` (`abm_refresh`): cookies are not isolated by port, so on
 * localhost the API and the app would otherwise share one name.
 */
export const REFRESH_COOKIE_NAME = "abm_bff_refresh";

export const LOGIN_PATH = "/login";
export const DEFAULT_AFTER_LOGIN_PATH = "/dashboard";

/** Paths that are reachable without a session. */
export const PUBLIC_PATHS: readonly string[] = [LOGIN_PATH];

/** Used when the refresh token carries no readable expiry (the backend default is 7 days). */
export const FALLBACK_REFRESH_MAX_AGE_SECONDS = 7 * 24 * 60 * 60;

/** Query parameter on /login saying why the user is there. */
export const REASON_PARAM = "reason";
export const REASON_EXPIRED = "expired";
