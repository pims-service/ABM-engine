import { expect, test } from "@playwright/test";

test("dashboard loads inside the app shell", async ({ page }) => {
  await page.goto("/dashboard");

  await expect(page.getByText("ABM Engine", { exact: true })).toBeVisible();
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("link", { name: "Dashboard" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
});

test("root redirects to the dashboard", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/dashboard$/);
});

test("skip link moves focus to the main content", async ({ page }) => {
  await page.goto("/dashboard");
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to main content" });
  await expect(skip).toBeFocused();
  await expect(skip).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main-content")).toBeFocused();
});

test("theme toggle switches to dark and survives a reload", async ({
  page,
}) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/dashboard");
  const html = page.locator("html");
  await expect(html).not.toHaveAttribute("data-theme", /.+/);

  await page.getByRole("button", { name: /theme: system/i }).click(); // light
  await page.getByRole("button", { name: /theme: light/i }).click(); // dark
  await expect(html).toHaveAttribute("data-theme", "dark");
  const bg = () =>
    page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  expect(await bg()).toBe("rgb(11, 15, 25)");

  await page.reload();
  await expect(html).toHaveAttribute("data-theme", "dark");
  expect(await bg()).toBe("rgb(11, 15, 25)");
});

test("follows prefers-color-scheme when no override is set", async ({
  page,
}) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/dashboard");
  expect(
    await page.evaluate(() => getComputedStyle(document.body).backgroundColor),
  ).toBe("rgb(11, 15, 25)");
});

test("sidebar collapses into a drawer on small screens", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto("/dashboard");

  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav).toBeHidden();

  const menu = page.getByRole("button", { name: "Menu" });
  await menu.click();
  await expect(nav).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(nav).toBeHidden();
  await expect(menu).toBeFocused();
});
