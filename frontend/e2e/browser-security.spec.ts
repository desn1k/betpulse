import { expect, test } from "@playwright/test";

const CSP_HEADER = "content-security-policy";

function nonceFrom(policy: string): string {
  const nonce = policy.match(/script-src 'nonce-([^']+)'/)?.[1];
  expect(nonce).toBeTruthy();
  return nonce ?? "";
}

test("document responses use a fresh strict CSP nonce", async ({ request }) => {
  const first = await request.get("/");
  const second = await request.get("/");

  expect(first.ok()).toBe(true);
  expect(second.ok()).toBe(true);

  const firstPolicy = first.headers()[CSP_HEADER];
  const secondPolicy = second.headers()[CSP_HEADER];
  expect(firstPolicy).toBeTruthy();
  expect(secondPolicy).toBeTruthy();

  const firstNonce = nonceFrom(firstPolicy);
  const secondNonce = nonceFrom(secondPolicy);
  expect(firstNonce).not.toBe(secondNonce);

  expect(firstPolicy).toContain(`script-src 'nonce-${firstNonce}' 'strict-dynamic' 'self';`);
  expect(firstPolicy).toContain(`style-src 'self' 'nonce-${firstNonce}';`);
  expect(firstPolicy).toContain("object-src 'none';");
  expect(firstPolicy).toContain("frame-ancestors 'none';");
  expect(firstPolicy).not.toContain("'unsafe-inline'");
  expect(firstPolicy).not.toContain("'unsafe-eval'");
});

test("security headers cover pages without adding document CSP to API responses", async ({
  request,
}) => {
  const pageResponse = await request.get("/");
  const apiResponse = await request.get("/api/health");

  expect(pageResponse.headers()["permissions-policy"]).toBe(
    "camera=(), microphone=(), geolocation=()",
  );
  expect(pageResponse.headers()["referrer-policy"]).toBe("strict-origin-when-cross-origin");
  expect(pageResponse.headers()["x-content-type-options"]).toBe("nosniff");
  expect(pageResponse.headers()["x-frame-options"]).toBe("DENY");

  expect(apiResponse.ok()).toBe(true);
  expect(apiResponse.headers()[CSP_HEADER]).toBeUndefined();
  expect(apiResponse.headers()["x-content-type-options"]).toBe("nosniff");
});

test("the production page boots without CSP violations", async ({ page }) => {
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

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.locator("html")).toHaveAttribute("lang", /^(ru|en)$/);
  await page.waitForTimeout(500);

  const policyViolations = await page.evaluate(() => {
    const target = window as typeof window & { __cspViolations?: string[] };
    return target.__cspViolations ?? [];
  });
  expect(consoleViolations).toEqual([]);
  expect(policyViolations).toEqual([]);
});

test("Accept-Language persists the initial locale cookie", async ({ browser }) => {
  const context = await browser.newContext({
    extraHTTPHeaders: { "Accept-Language": "en-US,en;q=0.9" },
  });
  const page = await context.newPage();

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  const localeCookie = (await context.cookies()).find((cookie) => cookie.name === "NEXT_LOCALE");
  expect(localeCookie?.value).toBe("en");
  expect(localeCookie?.sameSite).toBe("Lax");

  await context.close();
});
