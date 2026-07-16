import Link from "next/link";
import { useTranslations } from "next-intl";

import { AuthMenu } from "@/components/auth/AuthMenu";

import { LanguageSwitcher } from "./LanguageSwitcher";

export function Header() {
  const t = useTranslations();
  return (
    <header className="border-b border-border bg-surface">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-4">
        <Link href="/" className="flex items-center gap-2">
          <span className="text-lg font-extrabold tracking-tight text-brand">BetPulse</span>
          <span className="hidden text-sm text-muted sm:inline">{t("brand.tagline")}</span>
        </Link>
        <nav className="flex items-center gap-4">
          <Link href="/backtester" className="text-sm font-semibold text-muted-strong hover:text-foreground">
            {t("backtester.title")}
          </Link>
          <Link href="/performance" className="text-sm font-semibold text-muted-strong hover:text-foreground">
            {t("nav.performance")}
          </Link>
          <Link href="/settings" className="text-sm font-semibold text-muted-strong hover:text-foreground">
            {t("nav.settings")}
          </Link>
          <LanguageSwitcher />
          <AuthMenu />
        </nav>
      </div>
    </header>
  );
}
