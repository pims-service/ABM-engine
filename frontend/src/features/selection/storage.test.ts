import { afterEach, describe, expect, it, vi } from "vitest";

import { CAMPAIGN_1, CLIENT_A } from "@/test/fixtures";

import {
  EMPTY_SELECTION,
  readStoredSelection,
  SELECTION_STORAGE_KEY,
  writeStoredSelection,
} from "./storage";

afterEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("selection storage", () => {
  it("round-trips a selection", () => {
    writeStoredSelection({ clientId: CLIENT_A, campaignId: CAMPAIGN_1 });
    expect(readStoredSelection()).toEqual({
      clientId: CLIENT_A,
      campaignId: CAMPAIGN_1,
    });
  });

  it("returns an empty selection for missing, corrupt or malformed data", () => {
    expect(readStoredSelection()).toEqual(EMPTY_SELECTION);
    window.localStorage.setItem(SELECTION_STORAGE_KEY, "{not json");
    expect(readStoredSelection()).toEqual(EMPTY_SELECTION);
    window.localStorage.setItem(SELECTION_STORAGE_KEY, "42");
    expect(readStoredSelection()).toEqual(EMPTY_SELECTION);
  });

  it("drops ids that are not UUIDs and campaigns without a client", () => {
    window.localStorage.setItem(
      SELECTION_STORAGE_KEY,
      JSON.stringify({ clientId: "../admin", campaignId: CAMPAIGN_1 }),
    );
    expect(readStoredSelection()).toEqual(EMPTY_SELECTION);
    window.localStorage.setItem(
      SELECTION_STORAGE_KEY,
      JSON.stringify({ clientId: CLIENT_A, campaignId: "nope" }),
    );
    expect(readStoredSelection()).toEqual({
      clientId: CLIENT_A,
      campaignId: null,
    });
  });

  it("never throws when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("full", "QuotaExceededError");
    });
    expect(readStoredSelection()).toEqual(EMPTY_SELECTION);
    expect(() =>
      writeStoredSelection({ clientId: CLIENT_A, campaignId: null }),
    ).not.toThrow();
  });
});
