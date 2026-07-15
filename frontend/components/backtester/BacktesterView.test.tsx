import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/lib/auth/store";
import { renderWithProviders } from "@/test/test-utils";

import { BacktesterView } from "./BacktesterView";

describe("BacktesterView", () => {
  beforeEach(() => {
    useAuthStore.setState({
      accessToken: "tok",
      user: { id: "u1", email: "x@y.com", role: "user" },
      pending: false,
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    useAuthStore.setState({ accessToken: null, user: null, pending: false });
  });

  it("prompts for login when signed out", () => {
    useAuthStore.setState({ accessToken: null, user: null, pending: false });
    const { getByText } = renderWithProviders(<BacktesterView />, { locale: "en" });
    expect(getByText(/Log in to run the backtester/i)).toBeInTheDocument();
  });

  it("runs a backtest and renders results", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({
          bet_type: "1x2",
          pick: "home",
          matched_count: 5,
          win_count: 3,
          win_rate: 0.6,
          roi: 0.1,
          total_staked: 5,
          total_return: 5.5,
          equity_curve: [1, 2],
          max_drawdown: 0.5,
          win_rate_ci: { lower: 0.2, upper: 0.9, confidence: 0.95 },
          by_league: [],
          by_season: [],
          available_bet_types: ["1x2"],
          roi_disclaimer: true,
          small_sample_warning: true,
          walk_forward: false,
          out_of_sample_roi: null,
          folds: [],
        }),
      ),
    );
    const user = userEvent.setup();
    const { getByRole, findByText } = renderWithProviders(<BacktesterView />, { locale: "en" });
    await user.click(getByRole("button", { name: "Run backtest" }));
    // A unique results label proves the result rendered.
    expect(await findByText("Matched matches")).toBeInTheDocument();
  });

  it("shows the daily-limit message on a 403", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: { tier_required: "pro" } }), { status: 403 }),
      ),
    );
    const user = userEvent.setup();
    const { getByRole, findByRole } = renderWithProviders(<BacktesterView />, { locale: "en" });
    await user.click(getByRole("button", { name: "Run backtest" }));
    expect(await findByRole("alert")).toHaveTextContent(/Daily backtest limit reached/i);
  });
});
