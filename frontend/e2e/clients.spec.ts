import { expect, MOCK_API, test } from "./fixtures";

// These specs change the mock API's shared in-memory data, so they run one after another and
// start from the seed (see e2e/mock-data.mjs).
test.describe.configure({ mode: "serial" });

interface Ids {
  acme: string;
  globex: string;
  dach: string;
  uk: string;
  pilot: string;
  nordics: string;
}

let ids: Ids;

test.beforeEach(async () => {
  const response = await fetch(`${MOCK_API}/__data/reset`, { method: "POST" });
  ids = ((await response.json()) as { ids: Ids }).ids;
});

async function setRole(role: "admin" | "manager" | "viewer") {
  await fetch(`${MOCK_API}/__data/role`, {
    method: "POST",
    body: JSON.stringify({ role }),
  });
}

async function writes(): Promise<string[]> {
  const response = await fetch(`${MOCK_API}/__data/log`);
  return (await response.json()) as string[];
}

const switcher = (page: import("@playwright/test").Page) =>
  page.getByRole("group", { name: "Current client and campaign" });

test.describe("clients list", () => {
  test("lists clients, paginates and hides archived ones by default", async ({
    page,
  }) => {
    await page.goto("/clients");
    await expect(page.getByRole("heading", { name: "Clients" })).toBeVisible();
    const table = page.getByRole("table", { name: "Clients" });
    await expect(table.getByRole("link", { name: "Acme Corp" })).toBeVisible();
    await expect(table.getByRole("link", { name: "Old Co" })).toHaveCount(0);
    await expect(table.getByRole("row")).toHaveCount(11); // header + 10

    await page.getByRole("button", { name: "Next" }).click();
    await expect(page.getByText(/Showing 11.12 of 12/)).toBeVisible();
    await expect(table.getByRole("row")).toHaveCount(3); // header + 2
  });

  test("searches, filters by status and shows archived on demand", async ({
    page,
  }) => {
    await page.goto("/clients");
    const table = page.getByRole("table", { name: "Clients" });
    await expect(table.getByRole("link", { name: "Acme Corp" })).toBeVisible();

    await page.getByRole("searchbox", { name: "Search clients" }).fill("glob");
    await expect(table.getByRole("row")).toHaveCount(2);
    await expect(table.getByRole("link", { name: "Globex" })).toBeVisible();

    await page.getByRole("searchbox", { name: "Search clients" }).fill("old");
    await expect(page.getByText("No clients match")).toBeVisible();
    await page.getByRole("checkbox", { name: "Show archived" }).check();
    await expect(table.getByRole("link", { name: "Old Co" })).toBeVisible();
    await expect(table.getByRole("row", { name: /Old Co/ })).toContainText(
      "Archived",
    );

    await page.getByRole("searchbox", { name: "Search clients" }).fill("");
    await page.getByRole("checkbox", { name: "Show archived" }).uncheck();
    await expect(table.getByRole("link", { name: "Acme Corp" })).toBeVisible();
    await expect(table.getByRole("link", { name: "Old Co" })).toHaveCount(0);
    await expect(
      page.getByRole("checkbox", { name: "Show archived" }),
    ).not.toBeChecked();
    await page
      .getByRole("combobox", { name: "Status" })
      .selectOption("archived");
    await expect(
      page.getByRole("checkbox", { name: "Show archived" }),
    ).toBeChecked();
    await expect(table.getByRole("row")).toHaveCount(2);
    await expect(table.getByRole("link", { name: "Old Co" })).toBeVisible();
  });

  test("archives a client only after confirmation, and can restore it", async ({
    page,
  }) => {
    await page.goto("/clients");
    await page.getByRole("button", { name: "Archive Acme Corp" }).click();
    const dialog = page.getByRole("dialog", { name: "Archive Acme Corp?" });
    await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();

    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    expect(await writes()).toEqual([]);
    await expect(
      page.getByRole("button", { name: "Archive Acme Corp" }),
    ).toBeFocused();

    await page.getByRole("button", { name: "Archive Acme Corp" }).click();
    await dialog.getByRole("button", { name: "Archive client" }).click();
    await expect(page.getByText("Archived Acme Corp.")).toBeVisible();
    await expect(page.getByRole("link", { name: "Acme Corp" })).toHaveCount(0);
    expect(await writes()).toEqual([
      `POST /api/v1/clients/${ids.acme}/archive/`,
    ]);

    await page.getByRole("checkbox", { name: "Show archived" }).check();
    await page.getByRole("button", { name: "Restore Acme Corp" }).click();
    await page
      .getByRole("dialog", { name: "Restore Acme Corp?" })
      .getByRole("button", { name: "Restore client" })
      .click();
    await expect(page.getByText("Restored Acme Corp.")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Archive Acme Corp" }),
    ).toBeVisible();
  });

  test("creates a client and maps API validation errors to the field", async ({
    page,
  }) => {
    await page.goto("/clients");
    await page.getByRole("button", { name: "New client" }).click();
    const dialog = page.getByRole("dialog", { name: "New client" });
    await expect(dialog.getByRole("textbox", { name: "Name" })).toBeFocused();

    await dialog.getByRole("textbox", { name: "Name" }).fill("acme corp");
    await dialog.getByRole("button", { name: "Create client" }).click();
    await expect(
      dialog.getByText("A client with this name already exists."),
    ).toBeVisible();
    await expect(dialog.getByRole("textbox", { name: "Name" })).toHaveAttribute(
      "aria-invalid",
      "true",
    );

    await dialog.getByRole("textbox", { name: "Name" }).fill("Brand New Co");
    await dialog.getByRole("textbox", { name: "Notes" }).fill("Fresh");
    await dialog.getByRole("button", { name: "Create client" }).click();
    await expect(page.getByText("Created Brand New Co.")).toBeVisible();
    await page.getByRole("searchbox", { name: "Search clients" }).fill("brand");
    await expect(
      page.getByRole("link", { name: "Brand New Co" }),
    ).toBeVisible();
    await expect(page.getByText("Fresh")).toBeVisible();
  });

  test("edits a client", async ({ page }) => {
    await page.goto("/clients");
    await page.getByRole("button", { name: "Edit Globex" }).click();
    const dialog = page.getByRole("dialog", { name: "Edit client" });
    await dialog
      .getByRole("textbox", { name: "Name" })
      .fill("Globex International");
    await dialog.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Saved Globex International.")).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Globex International" }),
    ).toBeVisible();
  });

  test("a viewer is told off by the API and the actions disappear", async ({
    page,
  }) => {
    await setRole("viewer");
    await page.goto("/clients");
    await page.getByRole("button", { name: "Archive Acme Corp" }).click();
    await page
      .getByRole("dialog", { name: "Archive Acme Corp?" })
      .getByRole("button", { name: "Archive client" })
      .click();
    const dialog = page.getByRole("dialog", { name: "Archive Acme Corp?" });
    await expect(dialog.getByRole("alert")).toHaveText(
      "You do not have permission to do this.",
    );
    await dialog.getByRole("button", { name: "Cancel" }).click();
    // Roles are per client, so only this client's actions disappear (the others still try).
    await expect(
      page.getByRole("button", { name: "Archive Acme Corp" }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Edit Globex" }),
    ).toBeVisible();

    // Edit is a lower level: the first rejected edit hides it too.
    await page.getByRole("button", { name: "Edit Acme Corp" }).click();
    const edit = page.getByRole("dialog", { name: "Edit client" });
    await edit.getByRole("textbox", { name: "Name" }).fill("Acme Renamed");
    await edit.getByRole("button", { name: "Save changes" }).click();
    await expect(edit.getByRole("alert")).toHaveText(
      "You do not have permission to do this.",
    );
    await edit.getByRole("button", { name: "Cancel" }).click();
    await expect(
      page.getByRole("button", { name: "Edit Acme Corp" }),
    ).toHaveCount(0);
  });
});

test.describe("client detail", () => {
  test("shows the client with its campaigns underneath", async ({ page }) => {
    await page.goto("/clients");
    await page.getByRole("link", { name: "Acme Corp" }).click();
    await expect(page).toHaveURL(new RegExp(`/clients/${ids.acme}$`));
    await expect(
      page.getByRole("heading", { level: 1, name: "Acme Corp" }),
    ).toBeVisible();
    await expect(page.getByText("Key account")).toBeVisible();

    const table = page.getByRole("table", { name: "Campaigns" });
    await expect(table.getByText("DACH SaaS")).toBeVisible();
    await expect(table.getByText("UK Fintech")).toBeVisible();
    await expect(table.getByText("Archived Pilot")).toHaveCount(0);
    await expect(
      table.getByRole("columnheader", { name: "Client" }),
    ).toHaveCount(0);

    await page.getByRole("button", { name: "Set as current client" }).click();
    await expect(
      page.getByRole("main").getByText("Current client"),
    ).toBeVisible();
    await expect(
      switcher(page).getByRole("combobox", { name: "Current client" }),
    ).toHaveValue(ids.acme);
  });

  test("says so for a client that does not exist", async ({ page }) => {
    await page.goto("/clients/00000000-0000-4000-8000-00000000ffff");
    await expect(page.getByText("Client not found")).toBeVisible();
  });
});

test.describe("campaigns list", () => {
  test("shows status, ICP summary and profile version", async ({ page }) => {
    await page.goto("/campaigns");
    const table = page.getByRole("table", { name: "Campaigns" });
    const dach = table.getByRole("row", { name: /DACH SaaS/ });
    await expect(dach).toContainText("Active");
    await expect(dach).toContainText("Countries: DE, AT, CH");
    await expect(dach).toContainText("Industries: Software, Fintech +1");
    await expect(dach).toContainText("Size: 50–500 employees");
    await expect(dach).toContainText("v3");
    await expect(dach).toContainText("Acme Corp");
    const uk = table.getByRole("row", { name: /UK Fintech/ });
    await expect(uk).toContainText("Draft");
    await expect(uk).toContainText("Up to 200 employees");
    await expect(
      table.getByRole("row", { name: /Nordics Retail/ }),
    ).toContainText("SE, NO, DK, FI +1");
    await expect(table.getByText("Archived Pilot")).toHaveCount(0);
  });

  test("filters by client, status and archived", async ({ page }) => {
    await page.goto("/campaigns");
    const table = page.getByRole("table", { name: "Campaigns" });
    await expect(table.getByText("DACH SaaS")).toBeVisible();

    await page
      .getByRole("combobox", { name: "Client", exact: true })
      .selectOption({ label: "Globex" });
    await expect(table.getByRole("row")).toHaveCount(2);
    await expect(table.getByText("Nordics Retail")).toBeVisible();

    await page
      .getByRole("combobox", { name: "Client", exact: true })
      .selectOption({ label: "Acme Corp" });
    await page.getByRole("combobox", { name: "Status" }).selectOption("draft");
    await expect(table.getByRole("row")).toHaveCount(2);
    await expect(table.getByText("UK Fintech")).toBeVisible();

    await page
      .getByRole("combobox", { name: "Status" })
      .selectOption("archived");
    await expect(table.getByText("Archived Pilot")).toBeVisible();
  });

  test("clone opens the new campaign for editing", async ({ page }) => {
    await page.goto("/campaigns");
    await page.getByRole("button", { name: "Clone DACH SaaS" }).click();
    await expect(page).toHaveURL(
      /\/campaigns\/00000000-0000-4000-8000-\d{12}\/edit$/,
    );
    expect(page.url()).not.toContain(ids.dach);
    expect(await writes()).toEqual([
      `POST /api/v1/campaigns/${ids.dach}/clone/`,
    ]);
  });

  test("edit links to the edit route", async ({ page }) => {
    await page.goto("/campaigns");
    await expect(
      page.getByRole("link", { name: "Edit DACH SaaS" }),
    ).toHaveAttribute("href", `/campaigns/${ids.dach}/edit`);
  });

  test("activates a draft and archives with confirmation", async ({ page }) => {
    await page.goto("/campaigns");
    await page.getByRole("button", { name: "Activate UK Fintech" }).click();
    await expect(page.getByText("Activated UK Fintech.")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Activate UK Fintech" }),
    ).toHaveCount(0);

    await page.getByRole("button", { name: "Archive DACH SaaS" }).click();
    await page
      .getByRole("dialog", { name: "Archive DACH SaaS?" })
      .getByRole("button", { name: "Archive campaign" })
      .click();
    await expect(page.getByText("Archived DACH SaaS.")).toBeVisible();
    await expect(
      page.getByRole("table", { name: "Campaigns" }).getByText("DACH SaaS"),
    ).toHaveCount(0);
  });

  test("a viewer's rejected clone shows a message instead of navigating", async ({
    page,
  }) => {
    await setRole("viewer");
    await page.goto("/campaigns");
    await page.getByRole("button", { name: "Clone DACH SaaS" }).click();
    await expect(page.getByRole("main").getByRole("alert")).toHaveText(
      "You do not have permission to do this.",
    );
    await expect(page).toHaveURL(/\/campaigns$/);
    // Roles are per client: every action on that client's campaigns is gone ...
    await expect(
      page.getByRole("button", { name: "Clone DACH SaaS" }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Activate UK Fintech" }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("link", { name: "Edit DACH SaaS" }),
    ).toHaveCount(0);
    // ... other clients' campaigns still offer theirs until the API says no there too.
    await expect(
      page.getByRole("button", { name: "Clone Nordics Retail" }),
    ).toBeVisible();
  });
});

test.describe("responsive", () => {
  for (const [name, width, height] of [
    ["tablet", 768, 1024],
    ["laptop", 1366, 768],
  ] as const) {
    test(`the lists fit the page at ${name} width`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      for (const path of ["/clients", "/campaigns"]) {
        await page.goto(path);
        await expect(page.getByRole("table").first()).toBeVisible();
        // The table scrolls inside its card; the page itself must not scroll sideways.
        const overflow = await page.evaluate(
          () =>
            document.documentElement.scrollWidth -
            document.documentElement.clientWidth,
        );
        expect(overflow).toBeLessThanOrEqual(0);
        // The header switcher stays reachable.
        await expect(
          switcher(page).getByRole("combobox", { name: "Current client" }),
        ).toBeVisible();
      }
    });
  }
});

test.describe("switcher", () => {
  test("remembers the client and campaign across reloads and pages", async ({
    page,
  }) => {
    await page.goto("/dashboard");
    const client = switcher(page).getByRole("combobox", {
      name: "Current client",
    });
    const campaign = switcher(page).getByRole("combobox", {
      name: "Current campaign",
    });
    await expect(campaign).toBeDisabled();
    await expect(client.getByRole("option", { name: "Globex" })).toBeAttached();

    await client.selectOption({ label: "Globex" });
    await expect(campaign).toBeEnabled();
    await campaign.selectOption({ label: "Nordics Retail" });
    await expect(campaign).toHaveValue(ids.nordics);

    await page.reload();
    await expect(client).toHaveValue(ids.globex);
    await expect(campaign).toHaveValue(ids.nordics);

    // The whole app sees it: the campaign list marks the current campaign.
    await page
      .getByRole("navigation", { name: "Main" })
      .getByRole("link", { name: "Campaigns" })
      .click();
    await expect(
      page.getByRole("row", { name: /Nordics Retail/ }),
    ).toContainText("Current");
  });

  test("changing the client clears the campaign", async ({ page }) => {
    await page.goto("/dashboard");
    const client = switcher(page).getByRole("combobox", {
      name: "Current client",
    });
    const campaign = switcher(page).getByRole("combobox", {
      name: "Current campaign",
    });
    await expect(
      client.getByRole("option", { name: "Acme Corp" }),
    ).toBeAttached();
    await client.selectOption({ label: "Acme Corp" });
    await campaign.selectOption({ label: "DACH SaaS" });
    await client.selectOption({ label: "Globex" });
    await expect(campaign).toHaveValue("");
  });

  test("forgets a stored selection the API does not know", async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.setItem(
        "abm-selection",
        JSON.stringify({
          clientId: "00000000-0000-4000-8000-00000000ffff",
          campaignId: null,
        }),
      );
    });
    await page.goto("/dashboard");
    const client = switcher(page).getByRole("combobox", {
      name: "Current client",
    });
    await expect(
      client.getByRole("option", { name: "Acme Corp" }),
    ).toBeAttached();
    await expect(client).toHaveValue("");
    await expect
      .poll(() =>
        page.evaluate(() => window.localStorage.getItem("abm-selection")),
      )
      .toContain('"clientId":null');
  });
});
