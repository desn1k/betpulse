import { NextRequest, NextResponse } from "next/server";

import { LOCALE_COOKIE, LOCALE_COOKIE_MAX_AGE, resolveLocale } from "./i18n/config";
import {
  buildContentSecurityPolicy,
  createCspNonce,
  withCspRequestHeaders,
} from "./lib/server/csp";

function isApiPath(pathname: string): boolean {
  return pathname === "/api" || pathname.startsWith("/api/");
}

/**
 * Apply per-request CSP to rendered pages and persist the active locale.
 *
 * The CSP nonce is forwarded to Next.js through request headers and returned to
 * the browser on the response. API routes keep the locale behavior but do not
 * receive a document-only CSP header.
 */
export function middleware(request: NextRequest): NextResponse {
  let response: NextResponse;

  if (isApiPath(request.nextUrl.pathname)) {
    response = NextResponse.next();
  } else {
    const nonce = createCspNonce();
    const contentSecurityPolicy = buildContentSecurityPolicy(
      nonce,
      process.env.NODE_ENV === "development",
    );
    const requestHeaders = withCspRequestHeaders(request.headers, nonce, contentSecurityPolicy);

    response = NextResponse.next({
      request: {
        headers: requestHeaders,
      },
    });
    response.headers.set("Content-Security-Policy", contentSecurityPolicy);
  }

  const existing = request.cookies.get(LOCALE_COOKIE)?.value;
  if (!existing) {
    const locale = resolveLocale(undefined, request.headers.get("accept-language"));
    response.cookies.set(LOCALE_COOKIE, locale, {
      maxAge: LOCALE_COOKIE_MAX_AGE,
      sameSite: "lax",
      path: "/",
    });
  }

  return response;
}

export const config = {
  // Skip Next internals and static assets; run on everything else.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.).*)"],
};
