import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.PORT ?? 3000);
const MOCK_API_PORT = Number(process.env.MOCK_API_PORT ?? 8999);
const baseURL = process.env.E2E_BASE_URL ?? `http://localhost:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    // A stand-in for the Django auth API (the Next.js route handlers call it server side).
    {
      command: "node e2e/mock-api.mjs",
      url: `http://127.0.0.1:${MOCK_API_PORT}/__state`,
      reuseExistingServer: !process.env.CI,
      env: { MOCK_API_PORT: String(MOCK_API_PORT) },
    },
    // The app, unless E2E_BASE_URL points at one that is already running (start it with
    // API_INTERNAL_BASE_URL pointing at the mock API). A production build, not `next dev`: the dev
    // server compiles routes on first request and hot-reloads while a test is typing, which made
    // the sign-in specs flaky.
    ...(process.env.E2E_BASE_URL
      ? []
      : [
          {
            command: `npm run build && npm run start -- --port ${PORT}`,
            url: baseURL,
            reuseExistingServer: !process.env.CI,
            timeout: 300_000,
            env: {
              NEXT_PUBLIC_API_BASE_URL: `http://127.0.0.1:${MOCK_API_PORT}/api`,
              API_INTERNAL_BASE_URL: `http://127.0.0.1:${MOCK_API_PORT}/api`,
            },
          },
        ]),
  ],
});
