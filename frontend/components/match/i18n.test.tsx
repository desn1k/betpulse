import { describe, expect, it } from "vitest";

import { detailFixture } from "@/test/fixtures";
import { renderWithProviders } from "@/test/test-utils";

import { ConsensusBar } from "./ConsensusBar";

// The consensus heading is a good i18n probe: it is a plain translated label.
describe("i18n", () => {
  it("renders Russian by default", () => {
    const { getByText } = renderWithProviders(<ConsensusBar match={detailFixture} />, {
      locale: "ru",
    });
    expect(getByText("Консенсус")).toBeInTheDocument();
  });

  it("renders English when the locale is en", () => {
    const { getByText } = renderWithProviders(<ConsensusBar match={detailFixture} />, {
      locale: "en",
    });
    expect(getByText("Consensus")).toBeInTheDocument();
  });
});
