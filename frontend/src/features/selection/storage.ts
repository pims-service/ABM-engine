/** The remembered client/campaign. Plain module (no "use client"): safe to import anywhere. */

export const SELECTION_STORAGE_KEY = "abm-selection";

export interface StoredSelection {
  clientId: string | null;
  campaignId: string | null;
}

export const EMPTY_SELECTION: StoredSelection = {
  clientId: null,
  campaignId: null,
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function asId(value: unknown): string | null {
  return typeof value === "string" && UUID.test(value) ? value : null;
}

/**
 * Reads the stored selection. Anything unexpected (storage blocked, corrupt JSON, wrong shapes)
 * yields an empty selection; ids that are not UUIDs are dropped before they can reach a URL.
 * A campaign without a client is meaningless and is dropped too.
 */
export function readStoredSelection(): StoredSelection {
  try {
    const raw = window.localStorage.getItem(SELECTION_STORAGE_KEY);
    if (!raw) return EMPTY_SELECTION;
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return EMPTY_SELECTION;
    const clientId = asId((parsed as Record<string, unknown>).clientId);
    const campaignId = asId((parsed as Record<string, unknown>).campaignId);
    return clientId ? { clientId, campaignId } : EMPTY_SELECTION;
  } catch {
    return EMPTY_SELECTION;
  }
}

/** Best effort: private windows and blocked storage must never break the app. */
export function writeStoredSelection(selection: StoredSelection): void {
  try {
    window.localStorage.setItem(
      SELECTION_STORAGE_KEY,
      JSON.stringify(selection),
    );
  } catch {
    // Not persisted; the selection still works for this page view.
  }
}
