// @vitest-environment node
// Guard: the standalone image must not trace devDependencies. `typescript` is
// pulled in by the TypeScript config file at build time only; the CI audit
// gate checks production dependencies, so anything shipped must be one.
import { describe, expect, it } from "vitest";

import config from "./next.config";

describe("next.config", () => {
  it("builds a standalone server", () => {
    expect(config.output).toBe("standalone");
  });

  it("keeps typescript out of the standalone trace", () => {
    const excludes = config.outputFileTracingExcludes?.["*"] ?? [];
    expect(excludes).toContain("node_modules/typescript/**");
  });
});
