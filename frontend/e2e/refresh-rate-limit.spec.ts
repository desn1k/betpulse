import { expect, test } from "@playwright/test";

// F7 in a real browser: the session renewal is answered 429 (the per-IP refresh
// limit). The tab stays signed in, sends no refresh before Retry-After (plus at
// most 5 s of jitter) whatever the user does — focus, visibility, network back,
// list polls — and then exactly one, which renews the session. No backend: the
// BFF routes are answered here, the clock is Playwright's.

const USER = { id: "u1", email: "one@betpulse.dev", role: "user" };
const LIST = { items: [], total: 0, limit: 30, offset: 0, matches_remaining: null };
const RETRY_AFTER_S = 30;

test("a 429 on refresh keeps the session and is retried once after Retry-After", async ({
  page,
  context,
  baseURL,
}) => {
  const url = baseURL ?? "";
  await context.addCookies([
    { name: "bp_csrf", value: "c1", url },
    { name: "bp_age_ok", value: "1", url },
  ]);
  await page.clock.install({ time: new Date("2026-10-09T10:00:00Z") });

  let refreshes = 0;
  await page.route("**/api/auth/refresh", async (route) => {
    refreshes += 1;
    if (refreshes === 2) {
      await route.fulfill({
        status: 429,
        json: { detail: "Too many refresh requests" },
        headers: { "retry-after": String(RETRY_AFTER_S), "cache-control": "no-store" },
      });
      return;
    }
    await route.fulfill({
      json: { access_token: `tok-${refreshes}`, token_type: "bearer", expires_in: 900, user: USER },
      headers: { "cache-control": "no-store" },
    });
  });
  const listAuth: (string | null)[] = [];
  await page.route("**/api/matches**", async (route) => {
    listAuth.push(route.request().headers()["authorization"] ?? null);
    await route.fulfill({ json: LIST });
  });

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.getByText(USER.email)).toBeVisible();
  await expect.poll(() => refreshes).toBe(1);

  // The renewal timer fires 60 s before expiry (14:00) and meets the 429.
  await page.clock.fastForward("14:01");
  await expect.poll(() => refreshes).toBe(2);

  // Inside the window: everything that would renew is tried, nothing is sent.
  for (let second = 0; second < RETRY_AFTER_S - 2; second += 7) {
    await page.evaluate(() => {
      window.dispatchEvent(new Event("focus"));
      window.dispatchEvent(new Event("online"));
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await page.clock.fastForward(7_000);
  }
  expect(refreshes).toBe(2);
  // Still signed in, no "session expired".
  await expect(page.getByText(USER.email)).toBeVisible();
  await expect(page.getByText(/session has expired/i)).toHaveCount(0);

  // After Retry-After and the jitter: exactly one more refresh, and it renews.
  await page.clock.fastForward(10_000);
  await expect.poll(() => refreshes).toBe(3);
  await page.clock.fastForward(30_000);
  expect(refreshes).toBe(3);
  await page.clock.fastForward("01:00");
  await expect.poll(() => listAuth.at(-1)).toBe("Bearer tok-3");
  await expect(page.getByText(USER.email)).toBeVisible();
});
