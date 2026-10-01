import Link from "next/link";
import type { ReactNode } from "react";
import { getTranslations } from "next-intl/server";

import { currentLegalLocale } from "@/components/legal/LegalPage";
import { LEGAL_DRAFT } from "@/config/legal";
import { LEGAL_DOCUMENT_IDS, getLegalDocument } from "@/content/legal";

// Shared shell for /legal/*: document navigation, the draft notice and, for
// translations, a note that the Russian text prevails.
export default async function LegalLayout({ children }: { children: ReactNode }) {
  const locale = await currentLegalLocale();
  const t = await getTranslations("legal");

  return (
    <div className="flex flex-col gap-6 lg:flex-row lg:gap-10">
      <nav aria-label={t("navLabel")} className="lg:w-56 lg:flex-shrink-0">
        <ul className="flex flex-wrap gap-x-4 gap-y-2 text-sm lg:flex-col">
          {LEGAL_DOCUMENT_IDS.map((id) => (
            <li key={id}>
              <Link href={`/legal/${id}`} className="text-muted-strong hover:text-foreground">
                {getLegalDocument(locale, id).title}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        {LEGAL_DRAFT && (
          <p
            role="note"
            className="rounded-card border border-warn/40 bg-warn/10 p-4 text-sm font-medium text-warn"
          >
            {t("draftNotice")}
          </p>
        )}
        {locale !== "ru" && (
          <p role="note" className="text-sm italic text-muted-strong">
            {t("translationNotice")}
          </p>
        )}
        {children}
      </div>
    </div>
  );
}
