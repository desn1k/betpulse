import type { Metadata } from "next";
import { getLocale } from "next-intl/server";

import { fillLegalValues, legalValues } from "@/config/legal";
import { getLegalDocument, type LegalDocumentId } from "@/content/legal";
import { defaultLocale, isLocale, type Locale } from "@/i18n/config";

import { LegalDocument } from "./LegalDocument";

export async function currentLegalLocale(): Promise<Locale> {
  const locale = await getLocale();
  return isLocale(locale) ? locale : defaultLocale;
}

export async function legalMetadata(id: LegalDocumentId): Promise<Metadata> {
  const doc = getLegalDocument(await currentLegalLocale(), id);
  return {
    title: `${doc.title} — BetPulse`,
    description: fillLegalValues(doc.summary, legalValues()),
  };
}

/** Server-rendered legal page body for the request's locale. */
export async function LegalPage({ id }: { id: LegalDocumentId }) {
  const doc = getLegalDocument(await currentLegalLocale(), id);
  return <LegalDocument doc={doc} values={legalValues()} />;
}
