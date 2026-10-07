import { type Page } from "@playwright/test";

import { expect, MOCK_API, test } from "./fixtures";

const SKYLIGHT = "c0000000-0000-4000-8000-000000000001";
const VIEWER_CLIENT = "c0000000-0000-4000-8000-000000000003";
const READ_ONLY_CAMPAIGN = "d0000000-0000-4000-8000-000000000001";

/** A name no other test (they share one mock API and run in parallel) will use. */
function uniqueName(label: string) {
  return `${label} ${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
}

interface StoredCampaign {
  id: string;
  name: string;
  profile_version: number;
  profile: Record<string, unknown>;
  lastPatch?: Record<string, unknown>;
}

async function stored(name: string): Promise<StoredCampaign | undefined> {
  const response = await fetch(`${MOCK_API}/__campaigns`);
  const { campaigns } = (await response.json()) as {
    campaigns: StoredCampaign[];
  };
  return campaigns.find((c) => c.name === name);
}

const textbox = (page: Page, name: string) =>
  page.getByRole("textbox", { name, exact: true });

async function fillRequired(page: Page, name: string) {
  await page.getByLabel(/^Client/).selectOption({ label: "SkyLight" });
  await page.getByLabel(/^Campaign name/).fill(name);
  await page.getByLabel(/^Offer/).fill("B2B Outbound / Lead Generation");
}

async function createCampaign(page: Page, name: string) {
  await page.goto("/campaigns/new");
  await fillRequired(page, name);
  await page.getByRole("button", { name: "Create campaign" }).click();
  await expect(page).toHaveURL(/\/campaigns\/[^/]+\/edit\?created=1$/);
}

test.describe("create campaign", () => {
  test("a complete campaign is created end to end", async ({ page }) => {
    const name = uniqueName("KSA");
    await page.goto(`/campaigns/new?client=${SKYLIGHT}`);
    await expect(
      page.getByRole("heading", { name: "New campaign" }),
    ).toBeVisible();
    // ?client= preselects the client
    await expect(page.getByLabel(/^Client/)).toHaveValue(SKYLIGHT);

    await page.getByLabel(/^Campaign name/).fill(name);
    await page.getByLabel(/^Offer/).fill("B2B Outbound / Lead Generation");

    const countries = page.getByRole("combobox", { name: "Countries" });
    await countries.fill("saudi");
    await page.getByRole("option", { name: /Saudi Arabia/ }).click();
    await countries.fill("emirates");
    await page.getByRole("option", { name: /United Arab Emirates/ }).click();
    await expect(
      page.getByRole("button", { name: "Remove Saudi Arabia" }),
    ).toBeVisible();

    const industries = textbox(page, "Industries");
    await industries.fill("Financial Services");
    await industries.press("Enter");
    await industries.fill("SaaS,Accounting,");
    await expect(
      page.getByRole("button", { name: "Remove SaaS" }),
    ).toBeVisible();

    await page.getByLabel("Company size, minimum").fill("10");
    await page.getByLabel("Company size, maximum").fill("500");
    await page.getByRole("radio", { name: "B2B and B2C" }).check();

    await textbox(page, "Excluded industries").fill("Gambling,");
    await textbox(page, "Excluded company types").fill("Government,");
    await textbox(page, "Target departments").fill(
      "Sales,Business Development,",
    );

    const titles = textbox(page, "Preferred buyer titles");
    for (const title of ["CEO", "Founder", "VP BD"]) {
      await titles.fill(title);
      await titles.press("Enter");
    }
    // keyboard reordering: VP BD to the top
    await page.getByRole("button", { name: "Move VP BD up" }).press("Enter");
    await page.getByRole("button", { name: "Move VP BD up" }).press("Enter");
    await expect(
      page.getByRole("button", { name: "Move VP BD up" }),
    ).toBeDisabled();

    await page.getByRole("checkbox", { name: "Arabic" }).check();
    await page.getByRole("checkbox", { name: "English" }).check();
    await page.getByLabel("Qualification rules and notes").fill("No startups");

    await page.getByRole("button", { name: "Create campaign" }).click();

    await expect(page).toHaveURL(/\/campaigns\/[^/]+\/edit\?created=1$/);
    await expect(
      page.getByRole("status").filter({ hasText: "Campaign created" }),
    ).toHaveText("Campaign created as version 1.");
    // the edit page shows what was saved
    await expect(page.getByLabel(/^Campaign name/)).toHaveValue(name);
    await expect(page.getByLabel("Company size, maximum")).toHaveValue("500");
    await expect(page.getByText("Version 1").first()).toBeVisible();

    const saved = await stored(name);
    expect(saved?.profile).toMatchObject({
      offer: "B2B Outbound / Lead Generation",
      countries: ["SA", "AE"],
      industries: ["Financial Services", "SaaS", "Accounting"],
      company_size_min: 10,
      company_size_max: 500,
      business_model: "both",
      excluded_industries: ["Gambling"],
      excluded_company_types: ["Government"],
      target_departments: ["Sales", "Business Development"],
      preferred_buyer_titles: ["VP BD", "CEO", "Founder"],
      outreach_languages: ["ar", "en"],
      custom_rules: "No startups",
    });
  });

  test("client-side validation stops an empty submit and focuses the first problem", async ({
    page,
  }) => {
    await page.goto("/campaigns/new");
    await page.getByRole("button", { name: "Create campaign" }).click();

    const summary = page.getByRole("alert").filter({ hasText: "need fixing" });
    await expect(summary).toContainText("3 problems need fixing");
    await expect(page.getByLabel(/^Client/)).toBeFocused();
    await expect(page.getByLabel(/^Client/)).toHaveAttribute(
      "aria-invalid",
      "true",
    );

    await summary.getByRole("link", { name: "Offer" }).click();
    await expect(page.getByLabel(/^Offer/)).toBeFocused();
    await expect(page.getByText("Describe the offer.").first()).toBeVisible();

    await page.getByLabel("Company size, minimum").fill("500");
    await page.getByLabel("Company size, maximum").fill("10");
    await page.getByLabel("Company size, maximum").blur();
    await expect(
      page
        .getByText("Maximum company size must be at least the minimum.")
        .first(),
    ).toBeVisible();
  });

  test("API validation errors show next to the right fields", async ({
    page,
  }) => {
    const name = uniqueName("Dup");
    await createCampaign(page, name);

    // Same name again: a top-level API error on `name`.
    await page.goto("/campaigns/new");
    await fillRequired(page, name);
    await page.getByRole("button", { name: "Create campaign" }).click();
    const nameField = page.getByLabel(/^Campaign name/);
    await expect(nameField).toBeFocused();
    await expect(nameField).toHaveAccessibleDescription(
      /A campaign with this name already exists for this client\./,
    );
    await expect(page).toHaveURL(/\/campaigns\/new$/);

    // Errors nested under `profile` map to the form fields as well.
    await page.route("**/api/v1/campaigns/", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      await route.fulfill({
        status: 400,
        contentType: "application/json",
        body: JSON.stringify({
          error: {
            code: "validation_error",
            message: "Invalid input.",
            request_id: "r1",
            details: {
              profile: {
                offer: ["This field may not be blank."],
                company_size_max: [
                  "Maximum company size must be at least the minimum.",
                ],
                structured_rules: ["Structured rules are not supported yet."],
              },
            },
          },
        }),
        headers: { "Access-Control-Allow-Origin": "*" },
      });
    });
    await nameField.fill(uniqueName("Other"));
    await page.getByRole("button", { name: "Create campaign" }).click();
    await expect(page.getByLabel(/^Offer/)).toHaveAccessibleDescription(
      /This field may not be blank\./,
    );
    await expect(
      page.getByLabel("Company size, maximum"),
    ).toHaveAccessibleDescription(/at least the minimum/);
    await expect(
      page.getByRole("alert").filter({ hasText: "need fixing" }),
    ).toContainText(
      "structured_rules: Structured rules are not supported yet.",
    );
    await expect(page.getByLabel(/^Offer/)).toBeFocused();
  });

  test("a client the user can only view gives a read-only state", async ({
    page,
  }) => {
    await page.goto(`/campaigns/new?client=${VIEWER_CLIENT}`);
    await page.getByLabel(/^Campaign name/).fill(uniqueName("Nope"));
    await page.getByLabel(/^Offer/).fill("Offer");
    await page.getByRole("button", { name: "Create campaign" }).click();
    await expect(
      page.getByText("This campaign is read-only for you."),
    ).toBeVisible();
    await expect(page.getByLabel(/^Offer/)).toBeDisabled();
    await expect(
      page.getByRole("button", { name: "Create campaign" }),
    ).toHaveCount(0);
  });
});

test.describe("edit campaign", () => {
  test("changing the rules saves a new version and says so", async ({
    page,
  }) => {
    const name = uniqueName("Edit");
    await createCampaign(page, name);
    await expect(page.getByText("No note was left.")).toBeVisible();

    await page.getByLabel(/^Offer/).fill("A sharper offer");
    await page.getByLabel("What changed").fill("Sharper offer");
    await expect(page.getByText("Unsaved changes")).toBeVisible();
    await page.getByRole("button", { name: "Save changes" }).click();

    const status = page
      .getByRole("status")
      .filter({ hasText: "Saved as version 2." });
    await expect(status).toBeVisible();
    await expect(status).toBeFocused();
    await expect(page.getByText("Version 2").first()).toBeVisible();
    await expect(
      page.getByText("What changed in this version: Sharper offer"),
    ).toBeVisible();
    await expect(page.getByLabel("What changed")).toHaveValue("");
    await expect(page.getByText("Unsaved changes")).toHaveCount(0);
    await page.getByText("Version history (2)").click();
    await expect(
      page.getByRole("listitem").filter({ hasText: "Version 1" }),
    ).toBeVisible();

    const saved = await stored(name);
    expect(saved?.profile_version).toBe(2);
    expect(saved?.profile.offer).toBe("A sharper offer");
  });

  test("a note alone does not make a version", async ({ page }) => {
    const name = uniqueName("Same");
    await createCampaign(page, name);
    await page.getByLabel("What changed").fill("only a note");
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(
      page.getByText(/No changes\. The rules are identical to version 1/),
    ).toBeVisible();
    expect((await stored(name))?.profile_version).toBe(1);
  });

  test("sends only what changed", async ({ page }) => {
    const name = uniqueName("Patch");
    await createCampaign(page, name);
    await page.getByLabel("Company size, maximum").fill("900");
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Saved as version 2.")).toBeVisible();
    // The mock records the body it received (the browser streams it, so Playwright cannot).
    expect((await stored(name))?.lastPatch).toEqual({
      profile: { company_size_max: 900 },
    });
  });

  test("a viewer sees a read-only state when saving is refused", async ({
    page,
  }) => {
    await page.goto(`/campaigns/${READ_ONLY_CAMPAIGN}/edit`);
    await expect(page.getByLabel(/^Offer/)).toHaveValue("Something to look at");
    await page.getByLabel(/^Campaign name/).fill("Trying anyway");
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(
      page.getByText("This campaign is read-only for you."),
    ).toBeVisible();
    await expect(page.getByLabel(/^Campaign name/)).toBeDisabled();
  });

  test("an unknown campaign shows a not-found state", async ({ page }) => {
    await page.goto("/campaigns/d0000000-0000-4000-8000-0000000000ff/edit");
    await expect(
      page.getByRole("heading", { name: "Campaign not found" }),
    ).toBeVisible();
  });
});

test.describe("unsaved changes", () => {
  test("leaving through a link asks first", async ({ page }) => {
    await page.goto("/campaigns/new");
    await page.getByLabel(/^Campaign name/).fill("Draft");

    const messages: string[] = [];
    page.once("dialog", (dialog) => {
      messages.push(dialog.message());
      void dialog.dismiss();
    });
    await page.getByRole("link", { name: "Dashboard" }).click();
    await expect(page).toHaveURL(/\/campaigns\/new$/);
    expect(messages[0]).toContain("unsaved changes");
    await expect(page.getByLabel(/^Campaign name/)).toHaveValue("Draft");

    page.once("dialog", (dialog) => void dialog.accept());
    await page.getByRole("link", { name: "Dashboard" }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
  });

  test("a clean form leaves without a prompt", async ({ page }) => {
    await page.goto("/campaigns/new");
    page.on("dialog", () => {
      throw new Error("unexpected dialog");
    });
    await page.getByRole("link", { name: "Dashboard" }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
  });

  test("the browser's Back button asks too", async ({ page }) => {
    await page.goto("/dashboard");
    await page.getByRole("link", { name: "Campaigns" }).click();
    await page.goto("/campaigns/new");
    await page.getByLabel(/^Campaign name/).fill("Draft");

    page.once("dialog", (dialog) => void dialog.dismiss());
    await page.goBack();
    await expect(page).toHaveURL(/\/campaigns\/new$/);
    await expect(page.getByLabel(/^Campaign name/)).toHaveValue("Draft");
  });
});

test.describe("layout", () => {
  for (const [label, width, height] of [
    ["tablet", 768, 1024],
    ["laptop", 1280, 800],
  ] as const) {
    test(`fits a ${label} viewport without sideways scrolling`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height });
      await page.goto("/campaigns/new");
      await expect(
        page.getByRole("button", { name: "Create campaign" }),
      ).toBeVisible();
      const overflow = await page.evaluate(
        () =>
          document.documentElement.scrollWidth -
          document.documentElement.clientWidth,
      );
      expect(overflow).toBeLessThanOrEqual(0);
      const main = await page.getByRole("main").boundingBox();
      const form = await page.locator("form").boundingBox();
      expect(form!.x + form!.width).toBeLessThanOrEqual(
        main!.x + main!.width + 1,
      );
    });
  }
});
