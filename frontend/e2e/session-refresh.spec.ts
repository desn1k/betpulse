import { expect, test } from "@playwright/test";

// F9 in a real browser: real timers (driven by Playwright's clock) and a real
// page. The BFF auth and match routes are answered here, so no backend is
// needed. The access token lives 15 minutes; the page must renew it before it
// expires and never send the expired one (the public match routes would
// silently treat it as a guest).

const USER = { id: "u1", email: "one@betpulse.dev", role: "user" };
const LIST = { items: [], total: 0, limit: 30, offset: 0, matches_remaining: null };

test("a signed-in page renews its token before expiry and keeps using the new one", async ({
  page,
  context,
  baseURL,
}) => {
  const url = baseURL ?? "";
  await context.addCookies([
    // A refresh session exists (the CSRF cookie is what hydrate looks for)…
    { name: "bp_csrf", value: "c1", url },
    // …and the 18+ gate was already confirmed.
    { name: "bp_age_ok", value: "1", url },
  ]);
  await page.clock.install({ time: new Date("2026-10-08T10:00:00Z") });

  let refreshes = 0;
  await page.route("**/api/auth/refresh", async (route) => {
    refreshes += 1;
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
  await expect.poll(() => listAuth.at(-1)).toBe("Bearer tok-1");
  const beforeExpiry = listAuth.length;

  // 16 minutes pass: past the renewal point (14 min) and the expiry (15 min).
  await page.clock.fastForward("16:00");
  await expect.poll(() => refreshes).toBe(2);
  // The list polls every 60 s; let it fetch again.
  await page.clock.fastForward("01:00");
  await expect.poll(() => listAuth.length).toBeGreaterThan(beforeExpiry);

  // Every list request after the jump carried the renewed token, none the old.
  expect(listAuth.slice(beforeExpiry).every((auth) => auth === "Bearer tok-2")).toBe(true);
  await expect(page.getByText(USER.email)).toBeVisible();
});
