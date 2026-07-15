import { describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/test-utils";

import { ProbabilityBar } from "./ProbabilityBar";

describe("ProbabilityBar", () => {
  it("sizes each 1X2 segment by probability and labels them", () => {
    const { getByRole } = renderWithProviders(
      <ProbabilityBar probs={{ home: 0.5, draw: 0.3, away: 0.2 }} label="Consensus" />,
    );

    const bar = getByRole("img");
    expect(bar).toHaveAttribute(
      "aria-label",
      "Consensus: home 50%, draw 30%, away 20%",
    );

    // Three segments, widths proportional to probability.
    const segments = bar.querySelectorAll("div");
    expect(segments).toHaveLength(3);
    expect((segments[0] as HTMLElement).style.width).toBe("50%");
    expect((segments[1] as HTMLElement).style.width).toBe("30%");
    expect((segments[2] as HTMLElement).style.width).toBe("20%");
  });

  it("hides the inline label for very small segments", () => {
    const { getByRole } = renderWithProviders(
      <ProbabilityBar probs={{ home: 0.92, draw: 0.05, away: 0.03 }} label="Elo" />,
    );
    const segments = getByRole("img").querySelectorAll("div");
    expect(segments[0]).toHaveTextContent("92%");
    // draw (5%) and away (3%) are below the 12% label threshold.
    expect(segments[1]).toHaveTextContent("");
    expect(segments[2]).toHaveTextContent("");
  });
});
