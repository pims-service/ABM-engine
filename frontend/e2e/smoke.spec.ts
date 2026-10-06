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
