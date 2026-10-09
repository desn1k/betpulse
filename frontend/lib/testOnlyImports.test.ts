// @vitest-environment node
// Static guard: test-only code under e2e/ (the fake backend of
// e2e/support/fakeBackend.ts above all) is never imported by the app. Only the
// e2e specs themselves and playwright.config.ts may reach into e2e/.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const frontendRoot = fileURLToPath(new URL("..", import.meta.url));
const SKIPPED = new Set(["node_modules", ".next", "e2e", "test-results", "playwright-report"]);
// The guard itself holds example imports in its self-test.
const ALLOWED = new Set(["playwright.config.ts", "lib/testOnlyImports.test.ts"]);
const E2E_IMPORT = /(?:from\s+|import\s*\(\s*|require\s*\(\s*)["'][^"']*\be2e\//;

function sources(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (SKIPPED.has(entry)) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...sources(full));
    else if (/\.(ts|tsx|mjs|js)$/.test(entry)) out.push(full);
  }
  return out;
}

describe("test-only code", () => {
  it("nothing outside e2e/ and the Playwright config imports from e2e/", () => {
    const offenders = sources(frontendRoot)
      .map((file) => relative(frontendRoot, file).split("\\").join("/"))
      .filter((file) => !ALLOWED.has(file))
      .filter((file) => E2E_IMPORT.test(readFileSync(join(frontendRoot, file), "utf8")));
    expect(offenders).toEqual([]);
  });

  it("the guard itself recognises such an import", () => {
    expect(E2E_IMPORT.test('import { FakeBackend } from "../e2e/support/fakeBackend";')).toBe(true);
    expect(E2E_IMPORT.test('const x = await import("@/e2e/support/fakeBackend");')).toBe(true);
    expect(E2E_IMPORT.test('import { x } from "@/lib/e2eish";')).toBe(false);
  });
});
