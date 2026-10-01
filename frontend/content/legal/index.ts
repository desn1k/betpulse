import type { Locale } from "@/i18n/config";

import { en } from "./en";
import { ru } from "./ru";
import type { LegalDocument, LegalDocumentId, LegalDocuments } from "./types";

export { LEGAL_DOCUMENT_IDS } from "./types";
export type { LegalBlock, LegalDocument, LegalDocumentId, LegalSection } from "./types";

// Russian is authoritative; English is a courtesy translation.
export const LEGAL_DOCUMENTS: Record<Locale, LegalDocuments> = { ru, en };

export function getLegalDocument(locale: Locale, id: LegalDocumentId): LegalDocument {
  return LEGAL_DOCUMENTS[locale][id];
}
