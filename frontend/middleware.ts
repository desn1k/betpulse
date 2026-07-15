import { NextRequest, NextResponse } from "next/server";

import { LOCALE_COOKIE, LOCALE_COOKIE_MAX_AGE, resolveLocale } from "./i18n/config";

/**
 * Locale-persistence middleware. The active locale is resolved cookie-first,
 * then from the Accept-Language header (see `resolveLocale`). On the first visit
 * — when no cookie exists yet — we write the resolved locale into the cookie so
 * every later request (and SSR) is stable and header-independent. An existing
 * cookie is always respected and never overwritten here.
 */
export function middleware(request: NextRequest): NextResponse {
  const response = NextResponse.next();

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
