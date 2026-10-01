import Link from "next/link";
import { useTranslations } from "next-intl";

import { LEGAL_DOCUMENT_IDS } from "@/content/legal/types";

// Persistent footer disclaimer (spec §19) plus a link to every legal document.
export function Footer() {
  const t = useTranslations();
  return (
    <footer className="mt-12 border-t border-border bg-surface">
      <div className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-8">
        <nav aria-label={t("legal.navLabel")} className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
          {LEGAL_DOCUMENT_IDS.map((id) => (
            <Link key={id} href={`/legal/${id}`} className="text-muted-strong hover:text-foreground">
              {t(`footer.${id}`)}
            </Link>
          ))}
        </nav>
        <p className="text-xs leading-relaxed text-muted">{t("disclaimer.text")}</p>
      </div>
    </footer>
  );
}
