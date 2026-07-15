"use client";

import { useLocale } from "next-intl";
import { useRouter } from "next/navigation";
import { useTransition } from "react";

import { cn } from "@/lib/utils";
import { LOCALE_COOKIE, LOCALE_COOKIE_MAX_AGE, locales, type Locale } from "@/i18n/config";

/**
 * Language switcher. Persists the choice in the NEXT_LOCALE cookie (not just
 * client state) so the next SSR render — and the very next request — uses it,
 * then refreshes the route so server components re-render in the new locale.
 */
export function LanguageSwitcher() {
  const active = useLocale();
  const router = useRouter();
  const [isPending, startTransition] = useTransition();

  function switchTo(locale: Locale) {
    if (locale === active) return;
    document.cookie = `${LOCALE_COOKIE}=${locale}; path=/; max-age=${LOCALE_COOKIE_MAX_AGE}; samesite=lax`;
    startTransition(() => router.refresh());
  }

  return (
    <div className="flex items-center gap-1" role="group" aria-label="Language">
      {locales.map((locale) => (
        <button
          key={locale}
          type="button"
          onClick={() => switchTo(locale)}
          disabled={isPending}
          aria-pressed={locale === active}
          className={cn(
            "rounded-pill px-2.5 py-1 text-xs font-semibold uppercase transition-colors",
            locale === active
              ? "bg-brand text-white"
              : "text-muted-strong hover:bg-surface-muted",
          )}
        >
          {locale}
        </button>
      ))}
    </div>
  );
}
