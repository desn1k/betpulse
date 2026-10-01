import { describe, expect, it } from "vitest";

import { AGE_GATE_COOKIE } from "@/components/legal/AgeGate";
import { fillLegalValues, LEGAL_VALUES, legalValues } from "@/config/legal";
import { LOCALE_COOKIE } from "@/i18n/config";

import { LEGAL_DOCUMENT_IDS, LEGAL_DOCUMENTS, type LegalBlock } from "./index";

// Cookies the backend sets (backend/app/core/config.py: refresh_cookie_name,
// csrf_cookie_name) plus the ones the frontend sets. The privacy policy must
// describe every one of them.
const COOKIES_SET_BY_THE_APP = ["bp_refresh", "bp_csrf", AGE_GATE_COOKIE, LOCALE_COOKIE];

function blockStrings(block: LegalBlock): string[] {
  if (typeof block === "string") return [block];
  if ("list" in block) return block.list;
  return block.links.flatMap((link) => [link.label, link.href, link.description ?? ""]);
}

function documentText(locale: "ru" | "en", id: (typeof LEGAL_DOCUMENT_IDS)[number]): string {
  const doc = LEGAL_DOCUMENTS[locale][id];
  return [
    doc.title,
    doc.summary,
    ...doc.sections.flatMap((s) => [s.heading, ...s.blocks.flatMap(blockStrings)]),
  ].join("\n");
}

describe.each(LEGAL_DOCUMENT_IDS)("legal document %s", (id) => {
  it("has the same sections and block shapes in ru and en", () => {
    const shape = (locale: "ru" | "en") =>
      LEGAL_DOCUMENTS[locale][id].sections.map((s) => ({
        id: s.id,
        blocks: s.blocks.map((b) =>
          typeof b === "string" ? "p" : "list" in b ? `list:${b.list.length}` : `links:${b.links.map((l) => l.href).join(",")}`,
        ),
      }));
    expect(shape("en")).toEqual(shape("ru"));
  });

  it.each(["ru", "en"] as const)("resolves every placeholder token (%s)", (locale) => {
    const filled = fillLegalValues(documentText(locale, id), legalValues());
    expect(filled).not.toMatch(/\{\w+\}/);
  });

  it("only links to existing legal pages or external https sites", () => {
    for (const locale of ["ru", "en"] as const) {
      for (const section of LEGAL_DOCUMENTS[locale][id].sections) {
        for (const block of section.blocks) {
          if (typeof block === "string" || !("links" in block)) continue;
          for (const { href } of block.links) {
            const internal = href.match(/^\/legal\/(\w+)$/)?.[1];
            if (internal) expect(LEGAL_DOCUMENT_IDS).toContain(internal);
            else expect(href).toMatch(/^https:\/\//);
          }
        }
      }
    }
  });
});

describe("personal data processing policy (152-FZ art. 18.1)", () => {
  it("covers every required topic", () => {
    const sectionIds = LEGAL_DOCUMENTS.ru.privacy.sections.map((s) => s.id);
    for (const required of [
      "operator",
      "purposes",
      "actions",
      "cookies",
      "storage",
      "retention",
      "transfer",
      "security",
      "rights",
      "responsible-person",
    ]) {
      expect(sectionIds).toContain(required);
    }
  });

  it.each(["ru", "en"] as const)("describes every cookie the app sets (%s)", (locale) => {
    const cookies = LEGAL_DOCUMENTS[locale].privacy.sections.find((s) => s.id === "cookies");
    const text = cookies?.blocks.flatMap(blockStrings).join("\n") ?? "";
    for (const name of COOKIES_SET_BY_THE_APP) expect(text).toContain(name);
  });

  it("names the operator, RKN registry number, storage location and responsible person", () => {
    const text = documentText("ru", "privacy");
    for (const token of [
      "{operatorType}",
      "{operatorName}",
      "{operatorRegistration}",
      "{operatorAddress}",
      "{operatorEmail}",
      "{rknRegistryNumber}",
      "{serverLocation}",
      "{responsiblePerson}",
    ]) {
      expect(text).toContain(token);
    }
    expect(LEGAL_VALUES.serverLocation).toBe("[SERVER LOCATION IN RF]");
  });

  it("discloses the cross-border transfers to Telegram and browser push services", () => {
    const transfer = LEGAL_DOCUMENTS.ru.privacy.sections.find((s) => s.id === "transfer");
    const text = transfer?.blocks.flatMap(blockStrings).join("\n") ?? "";
    expect(text).toContain("Telegram");
    expect(text).toMatch(/push-сервисы/);
    expect(text).toContain("ст. 12");
  });
});

describe("consent document", () => {
  it("is a standalone page referenced from the terms rather than embedded in them", () => {
    expect(LEGAL_DOCUMENTS.ru.consent.title).toBe("Согласие на обработку персональных данных");
    expect(documentText("ru", "terms")).toContain("/legal/consent");
    expect(documentText("ru", "terms")).not.toContain("Я, пользователь сайта");
  });
});

describe("responsible gaming", () => {
  it.each(["ru", "en"] as const)("keeps 18+, help resources and the local placeholder (%s)", (locale) => {
    const text = documentText(locale, "responsible");
    expect(text).toContain("18");
    expect(text).toContain("https://www.gamblersanonymous.org/");
    expect(text).toContain("https://www.gamblingtherapy.org/");
    expect(text).toContain("[LOCAL RUSSIAN HELP RESOURCE]");
    expect(text.toLowerCase()).not.toMatch(/gamstop|gamcare|begambleaware/);
  });
});

describe("fillLegalValues", () => {
  it("rejects unknown placeholders instead of rendering them", () => {
    expect(() => fillLegalValues("{notAValue}", legalValues())).toThrow(/Unknown legal placeholder/);
    expect(() => fillLegalValues("{toString}", legalValues())).toThrow(/Unknown legal placeholder/);
  });
});
