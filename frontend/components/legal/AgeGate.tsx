"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/Button";

export const AGE_GATE_COOKIE = "bp_age_ok";

// Documents a visitor may need to read before confirming their age.
const GATE_DOCUMENTS = ["terms", "privacy", "consent", "responsible"] as const;

interface AgeGateProps {
  /** Whether the consent cookie was present at request time (read on the server). */
  consented: boolean;
  /** Cookie lifetime in days (from AGE_GATE_CONSENT_DAYS). */
  consentDays: number;
}

/**
 * 18+ age gate (spec §19). A full-screen overlay shown on the first visit until
 * the user accepts, which sets a consent cookie for `consentDays`. When the
 * cookie already exists the overlay never renders. With JavaScript disabled the
 * interactive overlay cannot run, so a static <noscript> warning is shown
 * instead (see AgeGateNoScript in the layout).
 *
 * Legal pages (/legal/*) are exempt so the terms, privacy policy and
 * responsible-gaming information can be read before confirming; the gate
 * returns on any other page until the visitor accepts.
 */
export function AgeGate({ consented, consentDays }: AgeGateProps) {
  const t = useTranslations();
  const pathname = usePathname();
  const [accepted, setAccepted] = useState(consented);

  if (accepted || pathname?.startsWith("/legal/")) return null;

  function accept() {
    const maxAge = consentDays * 24 * 60 * 60;
    document.cookie = `${AGE_GATE_COOKIE}=1; path=/; max-age=${maxAge}; samesite=lax`;
    setAccepted(true);
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="age-gate-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/70 p-4"
    >
      <div className="max-w-md rounded-card bg-surface p-8 text-center shadow-card">
        <span className="text-4xl" aria-hidden="true">
          🔞
        </span>
        <h2 id="age-gate-title" className="mt-4 text-xl font-bold text-foreground">
          {t("ageGate.title")}
        </h2>
        <p className="mt-2 text-sm leading-relaxed text-muted-strong">{t("ageGate.body")}</p>
        <Button size="lg" className="mt-6 w-full" onClick={accept}>
          {t("ageGate.accept")}
        </Button>
        <nav aria-label={t("ageGate.documentsLabel")} className="mt-4">
          <ul className="flex flex-wrap justify-center gap-x-4 gap-y-1 text-xs">
            {GATE_DOCUMENTS.map((id) => (
              <li key={id}>
                <Link href={`/legal/${id}`} className="text-muted-strong underline hover:text-foreground">
                  {t(`footer.${id}`)}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </div>
  );
}
