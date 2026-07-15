import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/test-utils";
import type { AnalysisResult } from "@/types/llm";

import { AnalysisBlock } from "./AnalysisBlock";

function stubAnalysis(body: unknown, status = 200) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify(body), {
          status,
          headers: { "content-type": "application/json" },
        }),
    ),
  );
}

const okResult: AnalysisResult = {
  status: "ok",
  content: "The models cluster above the market on a home win.",
  model: "test-model",
  language: "en",
  cached: false,
  not_a_probability_source: true,
  resets_at: null,
  is_match_of_the_day: false,
};

describe("AnalysisBlock", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders the narrative with the always-on disclaimer", async () => {
    stubAnalysis(okResult);
    const { findByText, getByText } = renderWithProviders(<AnalysisBlock id="abc" />, {
      locale: "en",
    });
    expect(await findByText(/cluster above the market/)).toBeInTheDocument();
    // The disclaimer renders regardless of the model text.
    expect(getByText(/not a source of probabilities/)).toBeInTheDocument();
  });

  it("shows the match-of-the-day badge when flagged", async () => {
    stubAnalysis({ ...okResult, is_match_of_the_day: true });
    const { findByText } = renderWithProviders(<AnalysisBlock id="abc" />, { locale: "en" });
    expect(await findByText(/Match of the day/)).toBeInTheDocument();
  });

  it("shows the tier lock on a 403", async () => {
    stubAnalysis({ detail: { error: "llm_requires_upgrade", tier_required: "pro" } }, 403);
    const { findByText } = renderWithProviders(<AnalysisBlock id="abc" />, { locale: "en" });
    expect(await findByText(/Available on the pro tier/)).toBeInTheDocument();
  });

  it("shows the reset time when the daily budget is exhausted", async () => {
    stubAnalysis({
      ...okResult,
      status: "budget_exhausted",
      content: null,
      resets_at: "2026-07-16T00:00:00+00:00",
    });
    const { findByText } = renderWithProviders(<AnalysisBlock id="abc" />, { locale: "en" });
    expect(await findByText(/quota is used up/)).toBeInTheDocument();
  });

  it("renders nothing when the feature is disabled", async () => {
    stubAnalysis({ ...okResult, status: "disabled", content: null });
    const { container, queryByText } = renderWithProviders(<AnalysisBlock id="abc" />, {
      locale: "en",
    });
    // Wait a tick for the query to settle, then assert the block collapsed.
    await vi.waitFor(() => expect(queryByText(/AI analysis/)).not.toBeInTheDocument());
    expect(container.querySelector("[role='note']")).toBeNull();
  });

  it("requests the analysis in the active locale", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL) =>
        new Response(JSON.stringify({ ...okResult, language: "ru" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<AnalysisBlock id="abc" />, { locale: "ru" });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(String(fetchMock.mock.calls[0][0])).toContain("language=ru");
  });
});
