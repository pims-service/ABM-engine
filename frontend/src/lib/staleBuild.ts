/** What a browser throws when a page script it asks for no longer exists on the server: a new
 * version was released while the page was open. */
const STALE_BUILD =
  /ChunkLoadError|Loading chunk|Loading CSS chunk|Failed to fetch dynamically imported module|error loading dynamically imported module/i;
const RELOADED_AT = "abm-reloaded-after-update";
/** Reload on our own at most once a minute, so a real failure cannot make a reload loop. */
const RELOAD_WINDOW_MS = 60_000;

export function isStaleBuildError(error: Error): boolean {
  return STALE_BUILD.test(`${error.name} ${error.message}`);
}

/** True when it is fine to reload now (and records that we did). */
export function mayReload(): boolean {
  try {
    const last = Number(window.sessionStorage.getItem(RELOADED_AT) ?? "0");
    if (Date.now() - last < RELOAD_WINDOW_MS) return false;
    window.sessionStorage.setItem(RELOADED_AT, String(Date.now()));
  } catch {
    // Storage can be blocked; then do not reload on our own.
    return false;
  }
  return true;
}
