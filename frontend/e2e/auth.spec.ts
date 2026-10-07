import {
  anonymousTest as test,
  expect,
  mockState,
  REFRESH_COOKIE,
  signIn,
  THROTTLED_EMAIL,
  VALID_EMAIL,
  VALID_PASSWORD,
} from "./fixtures";

const MOCK_API_REVOKE = `http://127.0.0.1:${process.env.MOCK_API_PORT ?? 8999}/__revoke`;

test.describe("protected routes", () => {
  test("a logged-out visit redirects to login and remembers the page", async ({
    page,
  }) => {
    await page.goto("/campaigns?status=active");
    await expect(page).toHaveURL(
      /\/login\?next=%2Fcampaigns%3Fstatus%3Dactive$/,
    );
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  });

  test("the root path is protected too", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/login\?next=%2F$/);
  });
});

test.describe("login", () => {
  test("signs in and lands on the page the user asked for", async ({
    page,
    context,
  }) => {
    await page.goto("/companies");
    await expect(page).toHaveURL(/\/login\?next=%2Fcompanies$/);

    await page.getByLabel("Email").fill(VALID_EMAIL);
    await page.getByLabel("Password").fill(VALID_PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();

    await expect(page).toHaveURL(/\/companies$/);
    await expect(page.getByTestId("current-user")).toHaveText("Ada Lovelace");

    // The refresh token is an httpOnly cookie; scripts cannot read it, and nothing is in storage.
    const cookie = (await context.cookies()).find(
      (c) => c.name === REFRESH_COOKIE,
    );
    expect(cookie).toMatchObject({ httpOnly: true, sameSite: "Lax" });
    expect(await page.evaluate(() => document.cookie)).not.toContain(
      REFRESH_COOKIE,
    );
    expect(
      await page.evaluate(() => localStorage.length + sessionStorage.length),
    ).toBe(0);
  });

  test("a wrong password shows an error and does not sign in", async ({
    page,
  }) => {
    await page.goto("/login");
    await page.getByLabel("Email").fill(VALID_EMAIL);
    await page.getByLabel("Password").fill("not-the-password");
    await page.getByRole("button", { name: "Sign in" }).click();

    await expect(page.getByRole("main").getByRole("alert")).toHaveText(
      "Incorrect email or password.",
    );
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByLabel("Password")).toHaveValue("");
  });

  test("validates the form before calling the API", async ({ page }) => {
    await page.goto("/login");
    await page.getByRole("button", { name: "Sign in" }).click();

    await expect(page.getByText("Enter your email address.")).toBeVisible();
    await expect(page.getByText("Enter your password.")).toBeVisible();
    await expect(page.getByLabel("Email")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    await expect(page.getByLabel("Email")).toBeFocused();
  });

  test("explains throttling", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Email").fill(THROTTLED_EMAIL);
    await page.getByLabel("Password").fill(VALID_PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("main").getByRole("alert")).toContainText(
      "Try again in 30 seconds",
    );
  });

  test("ignores a malicious next parameter", async ({ page }) => {
    await page.goto("/login?next=https%3A%2F%2Fevil.example%2Fsteal");
    await page.getByLabel("Email").fill(VALID_EMAIL);
    await page.getByLabel("Password").fill(VALID_PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
  });
});

test.describe("session", () => {
  test("a reload keeps the user signed in (silent refresh from the cookie)", async ({
    page,
    context,
    baseURL,
  }) => {
    await signIn(context, baseURL as string);
    await page.goto("/campaigns");
    await expect(page.getByTestId("current-user")).toHaveText("Ada Lovelace");
    await page.reload();
    await expect(page.getByTestId("current-user")).toHaveText("Ada Lovelace");
    await expect(page).toHaveURL(/\/campaigns$/);
  });

  test("a revoked session returns to login, then back to the same page", async ({
    page,
    context,
    baseURL,
  }) => {
    const refresh = await signIn(context, baseURL as string);
    await fetch(MOCK_API_REVOKE, {
      method: "POST",
      body: JSON.stringify({ refresh }),
    });

    await page.goto("/companies");
    await expect(page).toHaveURL(/\/login\?next=%2Fcompanies/);

    await page.getByLabel("Email").fill(VALID_EMAIL);
    await page.getByLabel("Password").fill(VALID_PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/companies$/);
  });
});

test.describe("logout", () => {
  test("signing out clears the cookie, blacklists the refresh token and protects the app again", async ({
    page,
    context,
    baseURL,
  }) => {
    await signIn(context, baseURL as string);
    await page.goto("/dashboard");
    await expect(page.getByTestId("current-user")).toBeVisible();
    // Loading the page rotated the seeded token; this is the one the browser holds now.
    const held = (await context.cookies()).find(
      (c) => c.name === REFRESH_COOKIE,
    )?.value as string;
    expect((await mockState()).validRefresh).toContain(held);

    await page.getByRole("button", { name: "Sign out" }).click();

    await expect(page).toHaveURL(/\/login$/);
    expect(
      (await context.cookies()).find((c) => c.name === REFRESH_COOKIE),
    ).toBeUndefined();

    const state = await mockState();
    expect(state.blacklistedRefresh).toContain(held);
    expect(state.validRefresh).not.toContain(held);

    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/login\?next=%2Fdashboard$/);
  });
});
