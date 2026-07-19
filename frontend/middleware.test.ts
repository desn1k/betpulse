import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { LOCALE_COOKIE } from "./i18n/config";
import { middleware } from "./middleware";

describe("middleware", () => {
  it("forwards a nonce to Next.js and returns the same CSP to the browser", () => {
    const request = new NextRequest("http://localhost/matches", {
      headers: { "accept-language": "ru" },
    });

    const response = middleware(request);
    const policy = response.headers.get("content-security-policy");
    const nonce = response.headers.get("x-middleware-request-x-nonce");

    expect(policy).toBeTruthy();
    expect(nonce).toBeTruthy();
    expect(response.headers.get("x-middleware-request-content-security-policy")).toBe(policy);
    expect(policy).toContain(`'nonce-${nonce}'`);
    expect(response.cookies.get(LOCALE_COOKIE)?.value).toBe("ru");
  });

  it("keeps document CSP off API responses while preserving locale persistence", () => {
    const request = new NextRequest("http://localhost/api/health", {
      headers: { "accept-language": "en" },
    });

    const response = middleware(request);

    expect(response.headers.get("content-security-policy")).toBeNull();
    expect(response.headers.get("x-middleware-request-x-nonce")).toBeNull();
    expect(response.cookies.get(LOCALE_COOKIE)?.value).toBe("en");
  });

  it("does not overwrite an existing locale cookie", () => {
    const request = new NextRequest("http://localhost/", {
      headers: { cookie: `${LOCALE_COOKIE}=en` },
    });

    const response = middleware(request);

    expect(response.cookies.get(LOCALE_COOKIE)).toBeUndefined();
  });
});
