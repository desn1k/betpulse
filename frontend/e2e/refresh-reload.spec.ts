import { expect, test } from "@playwright/test";

import { FakeBackend, USER } from "./support/fakeBackend";

// F13 part A in a real browser, through the real BFF (lib/server/authProxy.ts),
// against a test-only fake backend (e2e/support/fakeBackend.ts) that rotates a
// refresh token the moment the request arrives and answers 1.5 s later.
//
// The stand finding: a reload aborts the page-load refresh after the server
// rotated T1 → T2; the browser never stores T2, presents T1 again later, and the
// server revokes the whole family as reuse. With the refresh sent as keepalive,
// the browser finishes the request after the reload and stores T2.
//
// What this proves: in Chromium, a keepalive refresh survives a reload storm and
// its Set-Cookie lands even though the page that sent it is gone; no reuse
// follows. What it does not: Firefox or Safari, a network drop or a browser
// crash after the server committed, an answer slower than the reuse window
// (those are F13 part B), or the backend's exact rules (pytest covers those).

test.describe.configure({ mode: "serial" });

test("a reload storm during a page-load refresh never ends in reuse (F13)", async ({
  page,
  context,
  baseURL,
}) => {
  const fake = new FakeBackend();
  await fake.start();
  try {
    const url = baseURL ?? "";
    await context.addCookies([
      { name: "bp_refresh", value: fake.first, url, httpOnly: true, sameSite: "Strict" },
      { name: "bp_csrf", value: "c1", url, sameSite: "Strict" },
      { name: "bp_age_ok", value: "1", url },
      { name: "NEXT_LOCALE", value: "en", url },
    ]);

    // The page-load refresh reaches the server, which rotates T1 → T2 at once
    // and will answer in 1.5 s; reload while that answer is still on its way.
    await page.goto("/", { waitUntil: "commit" });
    await fake.rotations(1);
    for (let i = 0; i < 3; i += 1) await page.reload({ waitUntil: "commit" });
    await page.reload({ waitUntil: "domcontentloaded" });

    // Settled: either the family was revoked (the finding) or this page holds a
    // session again with a token that came after T1.
    await expect
      .poll(
        () =>
          fake.events.some((e) => e.type === "reuse")
            ? "reuse"
            : fake.events.some((e) => e.type === "rotated" && e.presented !== fake.first)
              ? "renewed"
              : "pending",
        { timeout: 20_000, intervals: [250] },
      )
      .not.toBe("pending");

    expect(fake.events.filter((e) => e.type === "reuse"), JSON.stringify(fake.events)).toEqual([]);
    // The jar ends on the family's live token (T2 or a successor), never on T1;
    // the newest answer lands 1.5 s after its rotation was recorded.
    const jar = async () => (await context.cookies()).find((c) => c.name === "bp_refresh")?.value;
    await expect.poll(jar, { timeout: 5_000 }).toBe(fake.liveToken);
    expect(await jar()).not.toBe(fake.first);
    await expect(page.getByText(USER.email)).toBeVisible();
  } finally {
    await fake.stop();
  }
});
