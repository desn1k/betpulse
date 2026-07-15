import { describe, expect, it } from "vitest";

import { summaryFixture } from "@/test/fixtures";
import { renderWithProviders } from "@/test/test-utils";

import { MatchCard, MATCH_CARD_HEIGHT } from "./MatchCard";
import { MatchCardSkeleton } from "./MatchCardSkeleton";

describe("MatchCard", () => {
  it("renders both teams, league and the consensus bar", () => {
    const { getByText, getByRole } = renderWithProviders(<MatchCard match={summaryFixture} />);
    expect(getByText("Arsenal")).toBeInTheDocument();
    expect(getByText("Chelsea")).toBeInTheDocument();
    expect(getByText("EPL")).toBeInTheDocument();
    // Consensus bar is an accessible image with the 1X2 breakdown.
    expect(getByRole("img")).toHaveAttribute("aria-label", expect.stringContaining("home 52%"));
  });

  it("shows the live badge and minute for live matches", () => {
    const live = {
      ...summaryFixture,
      status: "live" as const,
      minute: 63,
      home_score: 1,
      away_score: 0,
    };
    const { getByText } = renderWithProviders(<MatchCard match={live} />, { locale: "en" });
    expect(getByText(/Live/)).toBeInTheDocument();
    expect(getByText(/63'/)).toBeInTheDocument();
  });

  it("shows a data-delayed badge only when the fixture is stale", () => {
    const fresh = renderWithProviders(<MatchCard match={summaryFixture} />, { locale: "en" });
    expect(fresh.queryByText("Data delayed")).not.toBeInTheDocument();

    const delayed = renderWithProviders(
      <MatchCard match={{ ...summaryFixture, data_delayed: true }} />,
      { locale: "en" },
    );
    expect(delayed.getByText("Data delayed")).toBeInTheDocument();
  });

  it("skeleton matches the card height (no layout shift)", () => {
    const { getByTestId } = renderWithProviders(<MatchCardSkeleton />);
    const skeleton = getByTestId("match-card-skeleton");
    // Both the card and the skeleton use the shared fixed-height class.
    for (const cls of MATCH_CARD_HEIGHT.split(" ")) {
      expect(skeleton.className).toContain(cls);
    }
  });
});
