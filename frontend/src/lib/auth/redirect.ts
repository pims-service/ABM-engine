import {
  DEFAULT_AFTER_LOGIN_PATH,
  LOGIN_PATH,
  PUBLIC_PATHS,
  REASON_EXPIRED,
  REASON_PARAM,
} from "./constants";

const FAKE_ORIGIN = "http://app.invalid";

function hasUnsafeCharacter(value: string): boolean {
  for (const char of value) {
    const code = char.charCodeAt(0);
    if (char === "\\" || code <= 0x1f || code === 0x7f) return true;
  }
  return false;
}

export function isPublicPath(pathname: string): boolean {
  return PUBLIC_PATHS.some(
    (path) => pathname === path || pathname.startsWith(`${path}/`),
  );
}

/**
 * Validate a user-supplied `next` value so that logging in can only ever lead to a page of this
 * app (no open redirect). Accepts a same-origin path (with optional query and hash) and returns it;
 * anything else (absolute URLs, `//host`, backslash tricks, control characters, the login page
 * itself, BFF endpoints) returns `fallback`.
 */
export function safeNextPath(
  next: string | null | undefined,
  fallback: string = DEFAULT_AFTER_LOGIN_PATH,
): string {
  if (typeof next !== "string" || next === "" || next.length > 2048) {
    return fallback;
  }
  // Single leading slash only; backslashes and control characters are treated as slashes or
  // stripped by some browsers, so refuse them outright.
  if (!next.startsWith("/") || hasUnsafeCharacter(next)) {
    return fallback;
  }
  if (next.startsWith("//")) return fallback;

  let url: URL;
  try {
    url = new URL(next, FAKE_ORIGIN);
  } catch {
    return fallback;
  }
  if (url.origin !== FAKE_ORIGIN) return fallback;

  // A percent-encoded slash pair or backslash must not survive a later decode into `//host`.
  let decoded: string;
  try {
    decoded = decodeURIComponent(url.pathname);
  } catch {
    return fallback;
  }
  if (decoded.startsWith("//") || decoded.includes("\\")) return fallback;

  if (isPublicPath(url.pathname) || url.pathname.startsWith("/api/")) {
    return fallback;
  }
  return `${url.pathname}${url.search}${url.hash}`;
}

/** `/login?next=...` for the page the user was on; `reason=expired` explains a lost session. */
export function loginUrl(
  returnTo?: string | null,
  options: { expired?: boolean } = {},
): string {
  const params = new URLSearchParams();
  const safe = safeNextPath(returnTo, "");
  if (safe) params.set("next", safe);
  if (options.expired) params.set(REASON_PARAM, REASON_EXPIRED);
  const query = params.toString();
  return query ? `${LOGIN_PATH}?${query}` : LOGIN_PATH;
}
