import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/test-utils";

import { AgeGate } from "./AgeGate";

describe("AgeGate", () => {
  beforeEach(() => {
    document.cookie = "bp_age_ok=; path=/; max-age=0";
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
