import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PerformanceTable, type PerformanceData } from "./PerformanceTable";

describe("PerformanceTable", () => {
  it("shows a clear message when no evaluation has run", () => {
    render(<PerformanceTable data={{ status: "no_evaluation_yet" }} />);
    expect(screen.getByTestId("no-eval")).toBeInTheDocument();
  });

  it("renders methods and marks the champion", () => {
    const data: PerformanceData = {
      status: "ok",
      evaluated_at: "2026-07-14T00:00:00Z",
      champion: "dixon_coles",
      methods: [
        {
          method: "dixon_coles",
          status: "champion",
          accuracy_pct: 12.5,
          brier: 0.18,
          log_loss: 0.9,
          roi_vs_closing: 2.5,
          sample_count: 400,
          display_weight: 60,
          is_champion: true,
        },
      ],
    };
    render(<PerformanceTable data={data} />);
    expect(screen.getByText(/dixon_coles ★/)).toBeInTheDocument();
    expect(screen.getByText("12.50")).toBeInTheDocument();
    expect(screen.getByTestId("evaluated-at")).toHaveTextContent("2026-07-14");
  });
});
