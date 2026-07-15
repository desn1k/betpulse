import { describe, expect, it } from "vitest";

import { detailFixture } from "@/test/fixtures";
import { renderWithProviders } from "@/test/test-utils";

import { ConsensusBar } from "./ConsensusBar";
import { MethodBars } from "./MethodBars";

describe("ConsensusBar", () => {
  it("shows model agreement and delta-vs-market", () => {
    const { getByText } = renderWithProviders(<ConsensusBar match={detailFixture} />, {
      locale: "en",
    });
    expect(getByText(/Model agreement: 97.5%/)).toBeInTheDocument();
    expect(getByText(/Delta vs market: \+12.0 pp/)).toBeInTheDocument();
  });
});

describe("MethodBars", () => {
  it("renders the champion first with its accuracy and every method bar", () => {
    const { getAllByText, container } = renderWithProviders(
      <MethodBars methods={detailFixture.methods} tierRequired="pro" />,
      { locale: "en" },
    );
    const bars = container.querySelectorAll("[data-testid^='method-bar-']");
    expect(bars).toHaveLength(3);
    // Champion (lightgbm) sorts to the top and carries the star + accuracy.
    expect(bars[0]?.getAttribute("data-testid")).toBe("method-bar-lightgbm");
    expect(getAllByText(/★ 60.0%/).length).toBeGreaterThan(0);
  });

  it("blurs the bars and shows a lock when the tier is locked", () => {
    const { getByText } = renderWithProviders(
      <MethodBars methods={detailFixture.methods} locked tierRequired="pro" />,
      { locale: "en" },
    );
    expect(getByText(/Available on the pro tier/)).toBeInTheDocument();
  });
});
