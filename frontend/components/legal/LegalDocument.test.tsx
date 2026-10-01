import { render, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { legalValues } from "@/config/legal";
import { LEGAL_DOCUMENTS } from "@/content/legal";

import { LegalDocument } from "./LegalDocument";

const intl = vi.hoisted(() => ({ locale: "ru" }));
vi.mock("next-intl/server", () => ({ getLocale: async () => intl.locale }));

describe("LegalDocument", () => {
  it("renders one h1 and an anchored h2 per section", () => {
    const doc = LEGAL_DOCUMENTS.ru.privacy;
    const { getAllByRole, container } = render(<LegalDocument doc={doc} values={legalValues()} />);

    expect(getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(getAllByRole("heading", { level: 2 })).toHaveLength(doc.sections.length);
    expect(container.querySelector("section#cookies h2")).toHaveTextContent(/cookie/);
  });

  it("highlights unfilled operator placeholders", () => {
    const { container } = render(
      <LegalDocument doc={LEGAL_DOCUMENTS.ru.privacy} values={legalValues()} />,
    );
    const marks = Array.from(container.querySelectorAll("mark")).map((m) => m.textContent);
    expect(marks).toContain("[SERVER LOCATION IN RF]");
    expect(marks).toContain("[ROSKOMNADZOR OPERATOR REGISTRY NUMBER]");
  });

  it("opens external help resources safely in a new tab", () => {
    const { getByRole } = render(
      <LegalDocument doc={LEGAL_DOCUMENTS.en.responsible} values={legalValues()} />,
    );
    const link = getByRole("link", { name: "Gamblers Anonymous" });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });
});

describe("LegalPage", () => {
  it.each([
    ["ru", "Политика обработки персональных данных"],
    ["en", "Personal Data Processing Policy"],
  ])("renders the %s document for the request locale with matching metadata", async (locale, title) => {
    intl.locale = locale;
    const { LegalPage, legalMetadata } = await import("./LegalPage");

    const { getByRole } = render(await LegalPage({ id: "privacy" }));
    expect(getByRole("heading", { level: 1 })).toHaveTextContent(title);

    const metadata = await legalMetadata("privacy");
    expect(metadata.title).toBe(`${title} — BetPulse`);
    expect(metadata.description).not.toMatch(/\{\w+\}/);
  });

  it("uses the configured age-gate lifetime in the cookie section", async () => {
    vi.stubEnv("AGE_GATE_CONSENT_DAYS", "180");
    intl.locale = "en";
    const { LegalPage } = await import("./LegalPage");

    const { container } = render(await LegalPage({ id: "privacy" }));
    const cookies = container.querySelector("section#cookies");
    expect(within(cookies as HTMLElement).getByText(/bp_age_ok/)).toHaveTextContent("180 days");
    vi.unstubAllEnvs();
  });
});
