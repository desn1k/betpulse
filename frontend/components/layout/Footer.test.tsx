import { describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/test-utils";

import { Footer } from "./Footer";

describe("Footer", () => {
  it("links every legal document and shows the persistent disclaimer", () => {
    const { getByRole, getByText } = renderWithProviders(<Footer />, { locale: "ru" });
    const nav = getByRole("navigation", { name: "Правовые документы" });

    const links = Array.from(nav.querySelectorAll("a")).map((a) => a.getAttribute("href"));
    expect(links).toEqual([
      "/legal/terms",
      "/legal/privacy",
      "/legal/consent",
      "/legal/responsible",
      "/legal/disclaimer",
    ]);
    expect(getByRole("link", { name: "Согласие на обработку персональных данных" })).toBeVisible();
    expect(getByText(/носит исключительно аналитический/)).toBeInTheDocument();
  });
});
