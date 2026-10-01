// @vitest-environment node
// Static guard: every app/api route handler that talks to FastAPI must go
// through lib/server/backendProxy (directly or via authProxy), which forwards
// the bearer token and the Caddy-set client IP. A handler calling fetch() or
// reading API_BASE_URL itself would silently drop the client IP and put every
// guest into one shared quota bucket.
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const frontendRoot = fileURLToPath(new URL("../..", import.meta.url));
const apiRoot = join(frontendRoot, "app", "api");
const serverLibRoot = join(frontendRoot, "lib", "server");

// Route handlers that never call the backend. Add to this list only with a
// reason; anything here may not import a backend helper either.
const NO_BACKEND_ROUTES = new Set(["app/api/health/route.ts"]);

const BACKEND_HELPER_IMPORT = /from\s+["']@\/lib\/server\/(backendProxy|authProxy)["']/;
const DIRECT_BACKEND_ACCESS = [/\bfetch\s*\(/, /API_BASE_URL/, /backendBaseUrl/, /process\.env/];

function relativePath(file: string): string {
  return relative(frontendRoot, file).split("\\").join("/");
}

function routeHandlers(): string[] {
  return readdirSync(apiRoot, { recursive: true, encoding: "utf8" })
    .filter((entry) => /(^|[\\/])route\.ts$/.test(entry))
    .map((entry) => join(apiRoot, entry))
    .sort();
}

describe("app/api route handlers", () => {
  const handlers = routeHandlers();

  it("are discovered", () => {
    expect(handlers.length).toBeGreaterThan(40);
  });

  it.each(handlers.map((file) => [relativePath(file), file]))(
    "%s reaches the backend only through the shared helper",
    (name, file) => {
      const source = readFileSync(file, "utf8");

      for (const pattern of DIRECT_BACKEND_ACCESS) {
        expect(source, `${name} must not use ${pattern} directly`).not.toMatch(pattern);
      }
      if (NO_BACKEND_ROUTES.has(name)) {
        expect(source).not.toMatch(BACKEND_HELPER_IMPORT);
      } else {
        expect(source, `${name} must import @/lib/server/backendProxy or authProxy`).toMatch(
          BACKEND_HELPER_IMPORT,
        );
      }
    },
  );

  it("every allow-listed no-backend route still exists", () => {
    const names = new Set(handlers.map(relativePath));
    for (const name of NO_BACKEND_ROUTES) expect(names).toContain(name);
  });
});

describe("lib/server", () => {
  it("only backendProxy.ts performs backend requests", () => {
    const offenders = readdirSync(serverLibRoot)
      .filter((file) => file.endsWith(".ts") && !file.endsWith(".test.ts"))
      .filter((file) => file !== "backendProxy.ts")
      .filter((file) => {
        const source = readFileSync(join(serverLibRoot, file), "utf8");
        return /\bfetch\s*\(/.test(source) || /API_BASE_URL/.test(source);
      });

    expect(offenders).toEqual([]);
  });
});
