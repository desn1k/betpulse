// i18n configuration shared by the request config, middleware and the client
// language switcher. Russian is the product default (spec §1).

export const locales = ["ru", "en"] as const;
export type Locale = (typeof locales)[number];

export const defaultLocale: Locale = "ru";

// Cookie that persists the user's language choice. Read on the server before the
// Accept-Language header so SSR renders the chosen locale on the very first paint.
export const LOCALE_COOKIE = "NEXT_LOCALE";
// One year — the choice should survive well beyond a single session.
export const LOCALE_COOKIE_MAX_AGE = 60 * 60 * 24 * 365;

export function isLocale(value: string | undefined | null): value is Locale {
  return value != null && (locales as readonly string[]).includes(value);
}

/**
 * Resolve the active locale with an explicit precedence:
 *   1. the persisted cookie (an explicit user choice),
 *   2. the browser's Accept-Language header,
 *   3. the default locale.
 */
export function resolveLocale(
  cookieValue: string | undefined | null,
  acceptLanguage: string | undefined | null,
): Locale {
  if (isLocale(cookieValue)) {
    return cookieValue;
  }
  if (acceptLanguage) {
    for (const part of acceptLanguage.split(",")) {
      const tag = part.trim().split(";")[0]?.split("-")[0]?.toLowerCase();
      if (isLocale(tag)) {
        return tag;
      }
    }
  }
  return defaultLocale;
}
