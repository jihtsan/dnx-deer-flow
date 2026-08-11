import { expect, test } from "@playwright/test";

test.describe("Root route", () => {
  test("redirects to login", async ({ request }) => {
    const response = await request.get("/", { maxRedirects: 0 });

    expect(response.status()).toBe(307);
    expect(response.headers().location).toBe("/login");
  });

  test("keeps sign-up visible when status fails but honors Gateway rejection", async ({
    page,
  }) => {
    await page.route("**/api/v1/auth/setup-status", (route) =>
      route.fulfill({ status: 503 }),
    );
    await page.route("**/api/v1/auth/providers", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ providers: [] }),
      }),
    );
    await page.route("**/api/v1/auth/register", (route) =>
      route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({
          detail: {
            code: "registration_disabled",
            message: "Self-registration is disabled on this deployment",
          },
        }),
      }),
    );

    await page.goto("/login");

    const signUp = page.getByRole("button", { name: /sign up/i });
    await expect(signUp).toBeVisible();

    await signUp.click();
    await expect(
      page.getByRole("button", { name: "Create Account", exact: true }),
    ).toBeVisible();

    await page.getByLabel("Email", { exact: true }).fill("visitor@example.com");
    await page.getByLabel("Password", { exact: true }).fill("Tr0ub4dor3a!");

    const registrationResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/auth/register") &&
        response.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Create Account", exact: true })
      .click();

    expect((await registrationResponse).status()).toBe(403);
    await expect(
      page.getByText("Authentication failed", { exact: true }),
    ).toBeVisible();
    await expect(page).toHaveURL(/\/login$/);
  });
});
