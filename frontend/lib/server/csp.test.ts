import { describe, expect, it } from "vitest";

import {
  buildContentSecurityPolicy,
  createCspNonce,
  withCspRequestHeaders,
} from "./csp";

describe("content security policy", () => {
  it("builds a strict production policy around the request nonce", () => {
    const policy = buildContentSecurityPolicy("test-nonce", false);

    expect(policy).toContain("default-src 'self';");
    expect(policy).toContain("script-src 'nonce-test-nonce' 'strict-dynamic' 'self';");
    expect(policy).toContain("style-src 'self' 'nonce-test-nonce';");
    expect(policy).toContain("worker-src 'self' blob:;");
    expect(policy).toContain("object-src 'none';");
    expect(policy).toContain("base-uri 'self';");
    expect(policy).toContain("frame-ancestors 'none';");
    expect(policy).toContain("upgrade-insecure-requests;");
    expect(policy).not.toContain("'unsafe-inline'");
    expect(policy).not.toContain("'unsafe-eval'");
  });

  it("keeps only the Next.js development allowances outside production", () => {
    const policy = buildContentSecurityPolicy("dev-nonce", true);

    expect(policy).toContain("script-src 'nonce-dev-nonce' 'strict-dynamic' 'self' 'unsafe-eval';");
    expect(policy).toContain("style-src 'self' 'unsafe-inline';");
    expect(policy).not.toContain("upgrade-insecure-requests");
  });

  it("creates a fresh nonce and forwards it with the policy", () => {
    const firstNonce = createCspNonce();
    const secondNonce = createCspNonce();
    const policy = buildContentSecurityPolicy(firstNonce, false);
    const headers = withCspRequestHeaders(new Headers({ accept: "text/html" }), firstNonce, policy);

    expect(firstNonce).not.toBe(secondNonce);
    expect(headers.get("accept")).toBe("text/html");
    expect(headers.get("x-nonce")).toBe(firstNonce);
    expect(headers.get("content-security-policy")).toBe(policy);
  });
});
