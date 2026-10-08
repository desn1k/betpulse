// @vitest-environment node
// Static guard (F9): client code takes the bearer header only from the async
// gate in lib/auth/store.ts, which renews a token close to expiry first. A
// synchronous read of the token would send an expired one, which the public
// match routes silently treat as a guest.
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const frontendRoot = fileURLToPath(new URL("../..", import.meta.url));
const SOURCE_DIRS = ["app", "components", "lib"];
const STORE = "lib/auth/store.ts";

function sources(): string[] {
  return SOURCE_DIRS.flatMap((dir) =>
    readdirSync(join(frontendRoot, dir), { recursive: true, encoding: "utf8" })
      .filter((entry) => /\.(ts|tsx)$/.test(entry) && !/\.test\.(ts|tsx)$/.test(entry))
      .map((entry) => relative(frontendRoot, join(frontendRoot, dir, entry)).split("\\").join("/")),
  );
}

describe("bearer header", () => {
  it("is never read synchronously outside the auth store", () => {
    const offenders = sources().filter((file) => {
      if (file === STORE) return false;
      const text = readFileSync(join(frontendRoot, file), "utf8");
      return /\bauthHeader\s*\(/.test(text) || /getState\(\)\.accessToken/.test(text);
    });
    expect(offenders).toEqual([]);
  });

  // ER2-01: requests with a bearer go through authFetch, the one place that turns
  // a 401 + X-Session-Revoked into a renewal (or a sign-out). A fetch built on
  // authHeaders() elsewhere would ignore it.
  it("is attached only by authFetch: authHeaders() is not called outside the auth store", () => {
    const offenders = sources().filter((file) => {
      if (file === STORE) return false;
      return /\bauthHeaders\s*\(/.test(readFileSync(join(frontendRoot, file), "utf8"));
    });
    expect(offenders).toEqual([]);
  });
});
