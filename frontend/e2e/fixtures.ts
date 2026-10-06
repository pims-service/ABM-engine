import { type BrowserContext, expect, test as base } from "@playwright/test";

export const MOCK_API = `http://127.0.0.1:${process.env.MOCK_API_PORT ?? 8999}`;
export const REFRESH_COOKIE = "abm_bff_refresh";

// Fake credentials understood by e2e/mock-api.mjs only. pragma: allowlist secret
export const VALID_EMAIL = "ada@example.com";
export const VALID_PASSWORD = "correct-horse-battery-staple"; // pragma: allowlist secret
export const THROTTLED_EMAIL = "slow@example.com";

interface MockState {
  validRefresh: string[];
  blacklistedRefresh: string[];
  refreshCalls: number;
}

export async function mockState(): Promise<MockState> {
  const response = await fetch(`${MOCK_API}/__state`);
  return (await response.json()) as MockState;
}

/** Ask the mock API for a live session and put its refresh token in the browser's cookie jar. */
export async function signIn(
  context: BrowserContext,
  baseURL: string,
): Promise<string> {
  const response = await fetch(`${MOCK_API}/__seed`, { method: "POST" });
  const { refresh } = (await response.json()) as { refresh: string };
  await context.addCookies([
    { name: REFRESH_COOKIE, value: refresh, url: baseURL, httpOnly: true },
  ]);
  return refresh;
}

/** `test` whose pages start signed in (use `anonymousTest` for logged-out flows). */
export const test = base.extend({
  page: async ({ page, context, baseURL }, provide) => {
    await signIn(context, baseURL as string);
    await provide(page);
  },
});

export const anonymousTest = base;
export { expect };
