import Link from "next/link";
import { useTranslations } from "next-intl";

// Persistent footer disclaimer (spec §19) plus the legal page links.
const LEGAL_LINKS = [
  { href: "/legal/terms", key: "terms" },
  { href: "/legal/privacy", key: "privacy" },
  { href: "/legal/responsible", key: "responsible" },
  { href: "/legal/disclaimer", key: "disclaimer" },
] as const;

export function Footer() {
  const t = useTranslations();
  return (
    <footer className="mt-12 border-t border-border bg-surface">
      <div className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-8">
        <nav className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
          {LEGAL_LINKS.map(({ href, key }) => (
            <Link key={key} href={href} className="text-muted-strong hover:text-foreground">
              {t(`footer.${key}`)}
            </Link>
          ))}
        </nav>
        <p className="text-xs leading-relaxed text-muted">{t("disclaimer.text")}</p>
      </div>
    </footer>
  );
}
