import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/test-utils";

import { DisclaimerBanner } from "./DisclaimerBanner";

describe("DisclaimerBanner", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("shows the disclaimer text and collapses it, persisting to sessionStorage", async () => {
    const user = userEvent.setup();
    const { getByText, getByRole, queryByText } = renderWithProviders(<DisclaimerBanner />, {
      locale: "en",
    });

    expect(getByText(/Analytical and informational purposes only/)).toBeInTheDocument();

    await user.click(getByRole("button", { name: "Collapse" }));

    expect(queryByText(/Analytical and informational purposes only/)).not.toBeInTheDocument();
    expect(sessionStorage.getItem("bp_disclaimer_collapsed")).toBe("1");
  });
});
