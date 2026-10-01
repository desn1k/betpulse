// Structured legal texts. Strings may contain `{key}` tokens that are filled
// from config/legal.ts at render time, so operator details and retention
// periods live in exactly one place.

export const LEGAL_DOCUMENT_IDS = ["terms", "privacy", "consent", "responsible", "disclaimer"] as const;
export type LegalDocumentId = (typeof LEGAL_DOCUMENT_IDS)[number];

export interface LegalLink {
  label: string;
  href: string;
  description?: string;
}

/** A paragraph, a bulleted list, or a list of links. */
export type LegalBlock = string | { list: string[] } | { links: LegalLink[] };

export interface LegalSection {
  /** Stable anchor, identical across locales (e.g. `cookies`). */
  id: string;
  heading: string;
  blocks: LegalBlock[];
}

export interface LegalDocument {
  title: string;
  /** One-sentence summary used as the page meta description. */
  summary: string;
  sections: LegalSection[];
}

export type LegalDocuments = Record<LegalDocumentId, LegalDocument>;
