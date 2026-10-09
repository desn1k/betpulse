import { expect, test } from "@playwright/test";

// The 18+ confirmation is a cookie the gate sets in the browser and the root
// layout reads on the server. A confirmed visitor must never be asked again
// while the cookie lives: not after a reload, and not in the server HTML.
const GATE_MARKUP = 'id="age-gate-title"';

test("a visitor who confirmed their age is not asked again after a reload", async ({
  browser,
  baseURL,
}) => {
  const context = await browser.newContext();
  await context.addCookies([{ name: "NEXT_LOCALE", value: "en", url: baseURL ?? "" }]);
  const page = await context.newPage();

  await page.goto("/", { waitUntil: "domcontentloaded" });
  const gate = page.getByRole("dialog");
  await expect(gate).toBeVisible();
  // A click on the server-rendered button before React attached its handler is
  // lost; wait for the hydration marker (app/providers.tsx).
  await page.locator("html[data-hydrated]").waitFor({ state: "attached" });
  await gate.getByRole("button", { name: "I am 18 or older" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);

  const cookies = await context.cookies();
  expect(cookies.find((cookie) => cookie.name === "bp_age_ok")?.value).toBe("1");

  const response = await page.reload({ waitUntil: "domcontentloaded" });
  expect((await response?.text())?.includes(GATE_MARKUP), "server rendered the gate").toBe(false);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await context.close();
});

test("the server does not render the gate for a visitor with the consent cookie", async ({
  browser,
  baseURL,
}) => {
  const context = await browser.newContext();
  await context.addCookies([
    { name: "NEXT_LOCALE", value: "en", url: baseURL ?? "" },
    { name: "bp_age_ok", value: "1", url: baseURL ?? "" },
  ]);
  const page = await context.newPage();

  const response = await page.goto("/", { waitUntil: "domcontentloaded" });
  expect((await response?.text())?.includes(GATE_MARKUP), "server rendered the gate").toBe(false);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await context.close();
});
