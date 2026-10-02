import { describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/test-utils";
import type { BacktestResult } from "@/types/backtester";

import { BacktestResults } from "./BacktestResults";

const base: BacktestResult = {
  bet_type: "1x2",
  pick: "home",
  matched_count: 42,
  win_count: 20,
  win_rate: 0.476,
  roi: 0.083,
  total_staked: 42,
  total_return: 45.5,
  equity_curve: [1, 0.5, 1.5, 0.2],
  max_drawdown: 1.3,
  win_rate_ci: { lower: 0.33, upper: 0.62, confidence: 0.95 },
  by_league: [],
  by_season: [],
  available_bet_types: ["1x2", "total"],
  roi_disclaimer: true,
  small_sample_warning: true,
  evaluation_protocol: "historical_rule_simulation",
  odds_basis: "closing",
  season_split: false,
  season_splits: [],
};

describe("BacktestResults", () => {
  it("shows a yellow small-sample warning card above the results", () => {
    const { getByRole } = renderWithProviders(<BacktestResults result={base} />, { locale: "en" });
    const alert = getByRole("alert");
    expect(alert).toHaveTextContent(/only 42 matches matched/i);
    // Warning uses the warn colour utility (yellow), not a subtle badge.
    expect(alert.className).toMatch(/bg-warn/);
  });

  it("renders the inline ROI disclaimer next to the ROI figure", () => {
    // No small-sample banner here, so the only disclaimer is the inline one.
    const { getByText } = renderWithProviders(
      <BacktestResults result={{ ...base, small_sample_warning: false }} />,
      { locale: "en" },
    );
    expect(getByText("8.3%")).toBeInTheDocument();
    expect(getByText(/Past performance does not predict future results/i)).toBeInTheDocument();
  });

  it("shows the full spec §19 disclaimer with the results", () => {
    const { getByTestId } = renderWithProviders(<BacktestResults result={base} />, {
      locale: "en",
    });
    expect(getByTestId("backtest-disclaimer")).toHaveTextContent(
      /Analytical and informational purposes only.*18\+/,
    );
  });

  it("states that the result is a fixed-rule simulation, not an out-of-sample test", () => {
    const { getByTestId } = renderWithProviders(<BacktestResults result={base} />, {
      locale: "ru",
    });
    const note = getByTestId("backtest-protocol");
    expect(note).toHaveTextContent("Модель не переобучается, поэтому это не проверка вне выборки.");
    expect(note).toHaveTextContent("Прошлые результаты не гарантируют будущих.");
  });

  it("shows the per-season breakdown with the past-performance phrase", () => {
    const { getByTestId, queryByText } = renderWithProviders(
      <BacktestResults
        result={{
          ...base,
          small_sample_warning: false,
          season_split: true,
          season_splits: [
            { season: "2022-2023", matched_count: 10, roi: 0.1 },
            { season: "2023-2024", matched_count: 12, roi: 0.042 },
          ],
        }}
      />,
      { locale: "ru" },
    );
    const table = getByTestId("season-splits");
    expect(table).toHaveTextContent("Разбивка по сезонам");
    expect(table).toHaveTextContent("2022-2023");
    expect(table).toHaveTextContent("4.2%");
    expect(table).toHaveTextContent("Прошлые результаты не гарантируют будущих.");
    // No "out-of-sample" claim anywhere.
    expect(queryByText(/вне выборки:/i)).toBeNull();
  });

  it("renders the equity curve container", () => {
    const { getByTestId } = renderWithProviders(<BacktestResults result={base} />, {
      locale: "en",
    });
    expect(getByTestId("equity-chart")).toBeInTheDocument();
  });

  it("shows the empty state when nothing matched", () => {
    const { getByText } = renderWithProviders(
      <BacktestResults result={{ ...base, matched_count: 0 }} />,
      { locale: "en" },
    );
    expect(getByText(/No matches met these filters/i)).toBeInTheDocument();
  });
});
