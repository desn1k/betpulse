import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/test-utils";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh }),
}));

import { LanguageSwitcher } from "./LanguageSwitcher";

describe("LanguageSwitcher", () => {
  beforeEach(() => {
    refresh.mockClear();
    document.cookie = "NEXT_LOCALE=; path=/; max-age=0";
  });

  it("persists the chosen locale in a cookie and refreshes the route", async () => {
    const user = userEvent.setup();
    const { getByRole } = renderWithProviders(<LanguageSwitcher />, { locale: "ru" });

    // ru is active; switching to en writes the cookie and refreshes SSR.
    await user.click(getByRole("button", { name: "en" }));

    expect(document.cookie).toContain("NEXT_LOCALE=en");
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("marks the active locale as pressed", () => {
    const { getByRole } = renderWithProviders(<LanguageSwitcher />, { locale: "ru" });
    expect(getByRole("button", { name: "ru" })).toHaveAttribute("aria-pressed", "true");
    expect(getByRole("button", { name: "en" })).toHaveAttribute("aria-pressed", "false");
  });
});
