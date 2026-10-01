import { expect, test, type Page } from "@playwright/test";

import { LEGAL_DOCUMENT_IDS, LEGAL_DOCUMENTS, type LegalDocumentId } from "../content/legal";

// Every footer legal link, in both locales, must serve a 200 page with the
// document's heading, under the strict nonce CSP, without the age-gate overlay.
const LOCALES = ["ru", "en"] as const;

async function trackCspViolations(page: Page): Promise<() => Promise<string[]>> {
  const consoleViolations: string[] = [];
  page.on("console", (message) => {
    if (/content security policy|violates the following directive/i.test(message.text())) {
      consoleViolations.push(message.text());
    }
  });
  await page.addInitScript(() => {
    const target = window as typeof window & { __cspViolations?: string[] };
    target.__cspViolations = [];
    document.addEventListener("securitypolicyviolation", (event) => {
      target.__cspViolations?.push(`${event.effectiveDirective}: ${event.blockedURI}`);
    });
  });
  return async () => {
    const fromPage = await page.evaluate(() => {
      const target = window as typeof window & { __cspViolations?: string[] };
      return target.__cspViolations ?? [];
    });
    return [...consoleViolations, ...fromPage];
  };
}

for (const locale of LOCALES) {
  test(`footer legal links serve every document (${locale})`, async ({ browser, baseURL }) => {
    const context = await browser.newContext();
    await context.addCookies([{ name: "NEXT_LOCALE", value: locale, url: baseURL ?? "" }]);
    const page = await context.newPage();
    const violations = await trackCspViolations(page);

    await page.goto("/", { waitUntil: "domcontentloaded" });
    const hrefs = await page
      .locator("footer nav a")
      .evaluateAll((links) => links.map((link) => link.getAttribute("href")));
    expect(hrefs).toEqual(LEGAL_DOCUMENT_IDS.map((id) => `/legal/${id}`));

    for (const id of LEGAL_DOCUMENT_IDS as readonly LegalDocumentId[]) {
      const href = `/legal/${id}`;
      const response = await page.goto(href, { waitUntil: "domcontentloaded" });

      expect(response?.status(), href).toBe(200);
      await expect(page.locator("html")).toHaveAttribute("lang", locale);
      await expect(page.getByRole("heading", { level: 1 })).toHaveText(
        LEGAL_DOCUMENTS[locale][id].title,
      );
      await expect(page.getByRole("note").first()).toBeVisible();
      await expect(page.getByRole("dialog")).toHaveCount(0);
    }

    await page.waitForTimeout(300);
    expect(await violations()).toEqual([]);
    await context.close();
  });
}

test("the age gate still blocks non-legal pages and links to the documents", async ({ page }) => {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  const gate = page.getByRole("dialog");
  await expect(gate).toBeVisible();

  await gate.getByRole("link").first().click();
  await expect(page).toHaveURL(/\/legal\/terms$/);
  await expect(page.getByRole("dialog")).toHaveCount(0);
});
