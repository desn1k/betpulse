// Single source of operator details and retention periods used by the legal
// pages (frontend/content/legal). Every value in [BRACKETS] is a placeholder the
// operator must fill in, and every text must be reviewed by a lawyer
// specialising in Russian personal-data law (152-FZ) before launch.
//
// The Russian texts are authoritative; English is a courtesy translation.
import { LOCALE_COOKIE_MAX_AGE } from "@/i18n/config";

/** Shows the "draft — not legally reviewed" banner on every legal page. */
export const LEGAL_DRAFT = true;

export const LEGAL_VALUES = {
  // --- Operator (152-FZ art. 18.1: the policy must identify the operator) ---
  operatorType: "[OPERATOR TYPE: INDIVIDUAL ENTREPRENEUR / SELF-EMPLOYED / COMPANY / INDIVIDUAL]",
  operatorName: "[OPERATOR FULL NAME OR COMPANY NAME]",
  operatorRegistration: "[INN / OGRN / OGRNIP]",
  operatorAddress: "[OPERATOR POSTAL ADDRESS]",
  operatorEmail: "[CONTACT E-MAIL]",
  responsiblePerson: "[PERSON RESPONSIBLE FOR PERSONAL DATA PROCESSING]",
  rknRegistryNumber: "[ROSKOMNADZOR OPERATOR REGISTRY NUMBER]",
  serverLocation: "[SERVER LOCATION IN RF]",
  siteAddress: "[SITE ADDRESS]",
  effectiveDate: "[EFFECTIVE DATE]",

  // --- Retention periods (152-FZ art. 5 part 7, art. 21) ---
  retentionAccount: "[RETENTION PERIOD: ACCOUNT DATA AFTER ACCOUNT DELETION]",
  retentionAuditLog: "[RETENTION PERIOD: SECURITY AUDIT LOG]",
  retentionPush: "[RETENTION PERIOD: PUSH SUBSCRIPTIONS AFTER UNSUBSCRIBE]",
  retentionPromo: "[RETENTION PERIOD: PROMO CODE AND SUBSCRIPTION HISTORY]",
  retentionBackups: "[RETENTION PERIOD: BACKUPS]",

  // --- Facts taken from backend defaults (update if JWT_* settings change) ---
  refreshTokenDays: "30",
  accessTokenMinutes: "15",
  localeCookieDays: String(Math.round(LOCALE_COOKIE_MAX_AGE / 86_400)),
} as const;

/** Lifetime of the 18+ confirmation cookie, from AGE_GATE_CONSENT_DAYS (server only). */
export function ageGateConsentDays(): number {
  const raw = Number(process.env.AGE_GATE_CONSENT_DAYS);
  return Number.isFinite(raw) && raw > 0 ? raw : 30;
}

export type LegalValues = typeof LEGAL_VALUES & { ageGateDays: string };

/** Static values plus the ones resolved from the runtime configuration. */
export function legalValues(): LegalValues {
  return { ...LEGAL_VALUES, ageGateDays: String(ageGateConsentDays()) };
}

/** Replace every `{key}` token with its value. Unknown tokens throw. */
export function fillLegalValues(text: string, values: LegalValues): string {
  return text.replace(/\{(\w+)\}/g, (_, key: string) => {
    if (!Object.prototype.hasOwnProperty.call(values, key)) {
      throw new Error(`Unknown legal placeholder {${key}}`);
    }
    return values[key as keyof LegalValues];
  });
}
