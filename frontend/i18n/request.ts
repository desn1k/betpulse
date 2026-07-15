import { cookies, headers } from "next/headers";
import { getRequestConfig } from "next-intl/server";

import { LOCALE_COOKIE, resolveLocale } from "./config";

// next-intl request config (no URL-based i18n routing): the locale is resolved
// per request from the cookie first, then the Accept-Language header, then the
// default. This runs during SSR, so the first server render already matches the
// user's persisted choice — no locale flash on load.
export default getRequestConfig(async () => {
  const cookieStore = await cookies();
  const headerStore = await headers();

  const locale = resolveLocale(
    cookieStore.get(LOCALE_COOKIE)?.value,
    headerStore.get("accept-language"),
  );

  const messages = (await import(`../messages/${locale}.json`)).default;
  // Pin the time zone so server and client render kickoff times identically
  // (no hydration mismatch). Match times are shown in UTC for now.
  return { locale, messages, timeZone: "UTC" };
});
