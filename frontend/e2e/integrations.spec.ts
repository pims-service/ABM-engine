import { expect, MOCK_API, test } from "./fixtures";

// These specs change the mock API's integration data (e2e/mock-integrations.mjs), so they run one
// after another and start from its seed.
test.describe.configure({ mode: "serial" });

const ACME_ID = "c0000000-0000-4000-8000-000000000002";
// Fake secrets for the mock only.
const SECRET = "YOUR_E2E_FAKE_SECRET_9876"; // pragma: allowlist secret
const SECRET_2 = "YOUR_E2E_FAKE_SECRET_5555"; // pragma: allowlist secret

test.beforeEach(async ({ page }) => {
  await fetch(`${MOCK_API}/__integrations/reset`, { method: "POST" });
  await page.addInitScript((clientId) => {
    window.localStorage.setItem(
      "abm-selection",
      JSON.stringify({ clientId, campaignId: null }),
    );
  }, ACME_ID);
});

test.afterAll(async () => {
  await fetch(`${MOCK_API}/__integrations/reset`, { method: "POST" });
});

async function setRole(role: "admin" | "manager" | "viewer") {
  await fetch(`${MOCK_API}/__integrations/role`, {
    method: "POST",
    body: JSON.stringify({ role }),
  });
}

async function state(): Promise<
  Array<{
    name: string;
    secrets: Record<string, string>;
    status: string;
    archived_at: string | null;
    config: Record<string, unknown>;
  }>
> {
  return (await fetch(`${MOCK_API}/__integrations/state`)).json();
}

test("lists connections grouped by category from the sidebar link", async ({
  page,
}) => {
  await page.goto("/dashboard");
  await page.getByRole("link", { name: "Integrations" }).click();
  await expect(
    page.getByRole("heading", { name: "Integrations", level: 1 }),
  ).toBeVisible();
  for (const label of ["AI / LLM", "CRM", "Outreach", "Automation"]) {
    await expect(
      page.getByRole("heading", { level: 2, name: new RegExp(label) }),
    ).toBeVisible();
  }
  const llm = page.getByRole("row", { name: /Primary LLM/ });
  await expect(llm).toContainText("Active");
  await expect(llm).toContainText("Never tested");
  await expect(llm).toContainText("****");
  await expect(page.getByText("Retired Mailer")).toHaveCount(0);

  await page.getByRole("checkbox", { name: "Show archived" }).check();
  await expect(page.getByRole("row", { name: /Retired Mailer/ })).toContainText(
    "Archived",
  );
});

test("adds a connection with credentials and never shows the secret again", async ({
  page,
}) => {
  await page.goto("/settings/integrations");
  await page.getByRole("button", { name: "Add connection" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Add connection" });
  await dialog.getByLabel("Category").selectOption("company_data");
  await dialog.getByLabel(/Provider key/).fill("fake");
  await dialog.getByLabel(/^Name/).fill("Company search");
  const secret = dialog.getByLabel("Credential 1 secret");
  await expect(secret).toHaveAttribute("type", "password");
  await expect(secret).toHaveAttribute("autocomplete", "new-password");
  await secret.fill(SECRET);
  await dialog.getByRole("button", { name: "Add connection" }).click();

  await expect(page.getByText("Added Company search.")).toBeVisible();
  const row = page.getByRole("row", { name: /Company search/ });
  await expect(row).toContainText("****9876");
  await expect(page.locator("body")).not.toContainText(SECRET);
  expect(await page.content()).not.toContain(SECRET);
  const storage = await page.evaluate(() =>
    JSON.stringify({ ...window.localStorage, ...window.sessionStorage }),
  );
  expect(storage).not.toContain(SECRET);
  const saved = (await state()).find((r) => r.name === "Company search");
  expect(saved?.secrets).toEqual({ api_key: SECRET });
});

test("rejects a bad provider key and invalid JSON before sending", async ({
  page,
}) => {
  await page.goto("/settings/integrations");
  await page.getByRole("button", { name: "Add connection" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Add connection" });
  await dialog.getByLabel(/Provider key/).fill("Bad-Key");
  await dialog.getByLabel(/^Name/).fill("X");
  await dialog.getByLabel(/Settings/).fill("{nope");
  await dialog.getByRole("button", { name: "Add connection" }).click();
  await expect(dialog.getByText(/starting with a letter/)).toBeVisible();
  await expect(dialog.getByText(/valid JSON/)).toBeVisible();
  expect((await state()).some((r) => r.name === "X")).toBe(false);
});

test("replaces credentials from empty fields; only the masked hint is visible", async ({
  page,
}) => {
  await page.goto("/settings/integrations");
  await page
    .getByRole("button", { name: "Credentials for Primary LLM" })
    .click();
  const dialog = page.getByRole("dialog", { name: "Replace credentials" });
  await expect(dialog.getByLabel("Credential 1 secret")).toHaveValue("");
  await expect(dialog).not.toContainText("SEED-ONLY");
  await dialog.getByLabel("Credential 1 secret").fill(SECRET_2);
  await dialog.getByRole("button", { name: "Replace credentials" }).click();

  await expect(
    page.getByText("Saved the credentials of Primary LLM."),
  ).toBeVisible();
  await expect(page.getByRole("row", { name: /Primary LLM/ })).toContainText(
    "****5555",
  );
  expect(await page.content()).not.toContain(SECRET_2);
  expect((await state())[0]?.secrets).toEqual({ api_key: SECRET_2 });
});

test("clears credentials only after confirming", async ({ page }) => {
  await page.goto("/settings/integrations");
  await page
    .getByRole("button", { name: "Credentials for Primary LLM" })
    .click();
  await page.getByRole("button", { name: "Clear credentials" }).click();
  const confirm = page.getByRole("dialog", { name: /Clear credentials of/ });
  expect((await state())[0]?.secrets).not.toEqual({});
  await confirm.getByRole("button", { name: "Clear credentials" }).click();
  await expect(
    page.getByText("Cleared the credentials of Primary LLM."),
  ).toBeVisible();
  await expect(page.getByRole("row", { name: /Primary LLM/ })).toContainText(
    "Not set",
  );
  expect((await state())[0]?.secrets).toEqual({});
});

test("tests a connection: success, then a failure with a readable reason", async ({
  page,
}) => {
  await page.goto("/settings/integrations");
  await page
    .getByRole("button", { name: "Test connection Primary LLM" })
    .click();
  const row = page.getByRole("row", { name: /Primary LLM/ });
  await expect(row.getByText(/Test passed\. Fake provider/)).toBeVisible();

  // Make the fake provider fail, then test again.
  await page.getByRole("button", { name: "Edit Primary LLM" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit connection" });
  await dialog.getByLabel(/Settings/).fill('{"simulate": "auth_failed"}');
  await dialog.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("Saved Primary LLM.")).toBeVisible();

  await page
    .getByRole("button", { name: "Test connection Primary LLM" })
    .click();
  await expect(
    row.getByText(/Test failed\. Authentication failed/),
  ).toBeVisible();
  await expect(row).toContainText("Error");
  expect(await page.content()).not.toContain("SEED-ONLY");
});

test("says so when a provider has no test yet", async ({ page }) => {
  await page.goto("/settings/integrations");
  await page.getByRole("button", { name: "Add connection" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Add connection" });
  await dialog.getByLabel(/Provider key/).fill("brand_new");
  await dialog.getByLabel(/^Name/).fill("Mystery");
  await dialog.getByRole("button", { name: "Add connection" }).click();
  await page.getByRole("button", { name: "Test connection Mystery" }).click();
  await expect(
    page.getByText(/Not tested\. No test available for this provider yet\./),
  ).toBeVisible();
  await expect(page.getByText(/Test passed/)).toHaveCount(0);
});

test("archives and restores with confirmations", async ({ page }) => {
  await page.goto("/settings/integrations");
  await page.getByRole("button", { name: "Archive Shared CRM" }).click();
  await page.getByRole("button", { name: "Archive connection" }).click();
  await expect(page.getByText("Archived Shared CRM.")).toBeVisible();
  await expect(page.getByText("Shared CRM", { exact: true })).toHaveCount(0);

  await page.getByRole("checkbox", { name: "Show archived" }).check();
  await page.getByRole("button", { name: "Restore Shared CRM" }).click();
  await page.getByRole("button", { name: "Restore connection" }).click();
  await expect(page.getByText("Restored Shared CRM.")).toBeVisible();
  expect(
    (await state()).find((r) => r.name === "Shared CRM")?.archived_at,
  ).toBeNull();
});

test("a manager can look but not change", async ({ page }) => {
  await setRole("manager");
  await page.goto("/settings/integrations");
  await expect(page.getByRole("row", { name: /Primary LLM/ })).toBeVisible();
  // Not known to be read-only up front: the first refused write turns the page read-only.
  await page.getByRole("button", { name: "Add connection" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Add connection" });
  await dialog.getByLabel(/Provider key/).fill("fake");
  await dialog.getByLabel(/^Name/).fill("Nope");
  await dialog.getByRole("button", { name: "Add connection" }).click();
  await expect(dialog.getByText(/do not have permission/)).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByText(/Only admins can add/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Add connection" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: /Test connection/ }),
  ).toHaveCount(0);
});

test("a viewer gets a no-access state", async ({ page }) => {
  await setRole("viewer");
  await page.goto("/settings/integrations");
  await expect(page.getByText("No access to integrations")).toBeVisible();
  await expect(page.getByText("Primary LLM")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Integrations" })).toHaveCount(0);
});

test("asks for a client when none is selected", async ({ page }) => {
  await page.addInitScript(() =>
    window.localStorage.removeItem("abm-selection"),
  );
  await page.goto("/settings/integrations");
  await expect(page.getByText("Choose a client first")).toBeVisible();
});
