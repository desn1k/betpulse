import { expect, test } from "@playwright/test";

// ER2-01 in a real browser, without a backend: the password was changed on
// another device, so this tab's access token is revoked and its refresh family
// is gone. The next request answers 401 + X-Session-Revoked; the tab must sign
// out by itself, with the "session expired" notice, without a reload.

const USER = {
  id: "u1",
  email: "one@betpulse.dev",
  role: "user",
  totp_enabled: false,
  must_change_password: false,
  two_factor_required: false,
};
const MATCH_ID = "54c3e209-a1ad-42ce-b5cc-f74b0e1712d6";

test("a revoked session signs the tab out on its next request", async ({ page, context, baseURL }) => {
  const url = baseURL ?? "";
  await context.addCookies([
    { name: "bp_csrf", value: "c1", url },
    { name: "bp_age_ok", value: "1", url },
    { name: "NEXT_LOCALE", value: "ru", url },
  ]);

  let refreshes = 0;
  await page.route("**/api/auth/refresh", async (route) => {
    refreshes += 1;
    if (refreshes === 1) {
      return route.fulfill({
        json: { access_token: "tok-1", token_type: "bearer", expires_in: 900, user: USER },
        headers: { "cache-control": "no-store" },
      });
    }
    return route.fulfill({ status: 401, json: { detail: "Invalid refresh token" } });
  });
  const seen: string[] = [];
  await page.route(`**/api/matches/${MATCH_ID}**`, async (route) => {
    const bearer = route.request().headers()["authorization"];
    seen.push(bearer ?? "guest");
    // Like the API: only a request with the revoked token is told so.
    if (!bearer) return route.fulfill({ status: 404, json: { detail: "Match not found" } });
    return route.fulfill({
      status: 401,
      json: { detail: "Session revoked" },
      headers: { "x-session-revoked": "true" },
    });
  });

  await page.goto(`/matches/${MATCH_ID}`, { waitUntil: "domcontentloaded" });

  await expect(page.getByText("Сессия истекла — войдите снова.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Войти" })).toBeVisible();
  expect(seen[0]).toBe("Bearer tok-1");
  expect(refreshes).toBe(2); // page-load restore, then one forced renewal; no loop
});
