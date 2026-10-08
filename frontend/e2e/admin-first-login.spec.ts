import { expect, test, type Route } from "@playwright/test";

// F10 in a real browser, without a backend: the BFF auth routes are answered
// here by a small state machine that behaves like the API. The admin signs in
// with the initial password, is led to change it, sets up TOTP (QR code and the
// manual key), reaches the admin panel, then signs out and back in with a code.

const EMAIL = "owner@betpulse.app";
const INITIAL = "initial password 1";
const NEW = "brand new passphrase";
const SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP";
const URI = `otpauth://totp/BetPulse:owner%40betpulse.app?secret=${SECRET}&issuer=BetPulse`;
const CODE = "123456";

test("an admin's first sign-in: password change, TOTP setup, admin panel, sign-in with a code", async ({
  page,
  context,
  baseURL,
}) => {
  await context.addCookies([
    { name: "bp_age_ok", value: "1", url: baseURL ?? "" },
    { name: "NEXT_LOCALE", value: "ru", url: baseURL ?? "" },
  ]);

  const state = { password: INITIAL, mustChange: true, totp: false, pendingSecret: false };
  const user = () => ({
    id: "admin-1",
    email: EMAIL,
    role: "admin",
    totp_enabled: state.totp,
    must_change_password: state.mustChange,
    two_factor_required: true,
  });
  const session = () => ({ access_token: "tok", token_type: "bearer", expires_in: 900, user: user() });
  const bodyOf = (route: Route) => JSON.parse(route.request().postData() ?? "{}");
  const noStore = { "cache-control": "no-store" };
  const setupResponses: string[] = [];

  await page.route("**/api/auth/login", async (route) => {
    const body = bodyOf(route);
    if (body.email !== EMAIL || body.password !== state.password) {
      return route.fulfill({ status: 401, json: { detail: "Invalid credentials" }, headers: noStore });
    }
    if (state.totp && body.totp_code === undefined) {
      return route.fulfill({
        status: 401,
        json: { detail: "Two-factor code required" },
        headers: { ...noStore, "x-2fa-required": "true" },
      });
    }
    if (state.totp && body.totp_code !== CODE) {
      return route.fulfill({ status: 401, json: { detail: "Invalid credentials" }, headers: noStore });
    }
    return route.fulfill({ json: session(), headers: noStore });
  });
  await page.route("**/api/auth/change-password", async (route) => {
    const body = bodyOf(route);
    if (body.current_password !== state.password) {
      return route.fulfill({ status: 400, json: { detail: "Current password is incorrect" }, headers: noStore });
    }
    state.password = body.new_password;
    state.mustChange = false;
    return route.fulfill({ json: { detail: "Password changed" }, headers: noStore });
  });
  await page.route("**/api/auth/2fa/setup", async (route) => {
    state.pendingSecret = true;
    setupResponses.push(route.request().headers()["authorization"] ?? "");
    return route.fulfill({ json: { secret: SECRET, provisioning_uri: URI }, headers: noStore });
  });
  await page.route("**/api/auth/2fa/enable", async (route) => {
    if (!state.pendingSecret || bodyOf(route).code !== CODE) {
      return route.fulfill({ status: 400, json: { detail: "Invalid code" }, headers: noStore });
    }
    state.totp = true;
    return route.fulfill({ json: { detail: "Two-factor authentication enabled" }, headers: noStore });
  });
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: user(), headers: noStore }));
  await page.route("**/api/auth/logout", (route) => route.fulfill({ json: { detail: "Logged out" } }));
  await page.route("**/api/matches**", (route) =>
    route.fulfill({ json: { items: [], total: 0, limit: 30, offset: 0, matches_remaining: 3 } }),
  );
  await page.route("**/api/admin/**", (route) => route.fulfill({ json: [] }));

  // 1. Sign in with the initial password.
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "Войти" }).click();
  await page.locator("input[type=email]").fill(EMAIL);
  await page.locator("input[type=password]").fill(INITIAL);
  await page.getByRole("form", { name: "Войти" }).getByRole("button", { name: "Войти" }).click();
  await expect(page.getByText(EMAIL)).toBeVisible();

  // 2. The admin panel sends the admin to the forced password change.
  await page.getByRole("link", { name: "Панель администратора" }).click();
  await expect(page).toHaveURL(/\/account\/security$/);
  await expect(page.getByText("Перед началом работы смените стартовый пароль.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Настроить" })).toBeDisabled();
  await page.locator("input[name=current]").fill(INITIAL);
  await page.locator("input[name=next]").fill(NEW);
  await page.locator("input[name=repeat]").fill(NEW);
  await page.getByRole("button", { name: "Сменить пароль" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Пароль изменён." })).toBeVisible();

  // 3. TOTP setup: QR code and the manual key; the secret stays out of the URL
  //    and browser storage.
  await page.getByRole("button", { name: "Настроить" }).click();
  await expect(page.getByRole("img", { name: "QR-код для приложения-аутентификатора" })).toBeVisible();
  await expect(page.getByText("JBSW Y3DP EHPK 3PXP JBSW Y3DP EHPK 3PXP")).toBeVisible();
  expect(page.url()).not.toContain(SECRET);
  const stored = await page.evaluate(() =>
    JSON.stringify({ ...localStorage, ...sessionStorage }),
  );
  expect(stored).not.toContain(SECRET);
  expect(setupResponses).toEqual(["Bearer tok"]);
  await page.getByLabel("Код из приложения").fill(CODE);
  await page.getByRole("button", { name: "Подтвердить" }).click();
  await expect(page.getByText("Двухфакторная аутентификация включена")).toBeVisible();

  // 4. The admin panel opens now.
  await page.getByRole("main").getByRole("link", { name: "Панель администратора" }).click();
  await expect(page).toHaveURL(/\/admin\/providers$/);
  await expect(page.getByRole("navigation").getByRole("link", { name: "Провайдеры" })).toBeVisible();

  // 5. Sign out, then sign in again: the form asks for the code after the password.
  await page.getByRole("button", { name: "Выйти" }).click();
  await page.getByRole("button", { name: "Войти" }).click();
  await page.locator("input[type=email]").fill(EMAIL);
  await page.locator("input[type=password]").fill(NEW);
  await page.getByRole("form", { name: "Войти" }).getByRole("button", { name: "Войти" }).click();
  await page.getByLabel("Код из приложения").fill("000000");
  await page.getByRole("form", { name: "Войти" }).getByRole("button", { name: "Войти" }).click();
  await expect(page.getByRole("form", { name: "Войти" }).getByRole("alert")).toHaveText(
    "Неверный код. Попробуйте ещё раз.",
  );
  await page.getByLabel("Код из приложения").fill(CODE);
  await page.getByRole("form", { name: "Войти" }).getByRole("button", { name: "Войти" }).click();
  await expect(page.getByText(EMAIL)).toBeVisible();
});
