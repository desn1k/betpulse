import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/test-utils";

import { AgeGate } from "./AgeGate";

const navigation = vi.hoisted(() => ({ pathname: "/" }));
vi.mock("next/navigation", () => ({ usePathname: () => navigation.pathname }));

describe("AgeGate", () => {
  beforeEach(() => {
    document.cookie = "bp_age_ok=; path=/; max-age=0";
    navigation.pathname = "/";
  });

  it("links to the legal documents a visitor may want to read first", () => {
    const { getByRole } = renderWithProviders(<AgeGate consented={false} consentDays={30} />, {
      locale: "ru",
    });
    for (const [name, href] of [
      ["Пользовательское соглашение", "/legal/terms"],
      ["Политика обработки персональных данных", "/legal/privacy"],
      ["Согласие на обработку персональных данных", "/legal/consent"],
      ["Ответственная игра", "/legal/responsible"],
    ]) {
      expect(getByRole("link", { name })).toHaveAttribute("href", href);
    }
  });

  it("stays out of the way on legal pages until the visitor leaves them", () => {
    navigation.pathname = "/legal/privacy";
    const legal = renderWithProviders(<AgeGate consented={false} consentDays={30} />);
    expect(legal.queryByRole("dialog")).not.toBeInTheDocument();
    legal.unmount();

    navigation.pathname = "/matches/1";
    const other = renderWithProviders(<AgeGate consented={false} consentDays={30} />);
    expect(other.getByRole("dialog")).toBeInTheDocument();
  });

  it("writes the consent cookie with the configured lifetime so it re-prompts on expiry", async () => {
    const user = userEvent.setup();
    const cookieSetter = vi.spyOn(document, "cookie", "set");
    const { getByRole } = renderWithProviders(<AgeGate consented={false} consentDays={180} />, {
      locale: "en",
    });

    await user.click(getByRole("button", { name: /18 or older/ }));

    expect(cookieSetter).toHaveBeenCalledWith(expect.stringContaining(`max-age=${180 * 86_400}`));
    cookieSetter.mockRestore();
  });

  it("shows the overlay when consent is absent", () => {
    const { getByRole } = renderWithProviders(
      <AgeGate consented={false} consentDays={30} />,
      { locale: "en" },
    );
    expect(getByRole("dialog")).toBeInTheDocument();
  });

  it("does not render when consent already exists", () => {
    const { queryByRole } = renderWithProviders(
      <AgeGate consented consentDays={30} />,
      { locale: "en" },
    );
    expect(queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("accepting sets the consent cookie and dismisses the overlay", async () => {
    const user = userEvent.setup();
    const { getByRole, queryByRole } = renderWithProviders(
      <AgeGate consented={false} consentDays={30} />,
      { locale: "en" },
    );

    await user.click(getByRole("button", { name: /18 or older/ }));

    expect(document.cookie).toContain("bp_age_ok=1");
    expect(queryByRole("dialog")).not.toBeInTheDocument();
  });
});
