import { fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/test-utils";
import type { IngestionRun, IngestionRuns } from "@/types/admin";

import { IngestionView, nextPollInterval } from "./IngestionView";

vi.mock("@/lib/admin", () => ({
  fetchIngestionRuns: vi.fn(),
  rescan: vi.fn(() => Promise.resolve()),
}));

import { fetchIngestionRuns, rescan } from "@/lib/admin";

function run(overrides: Partial<IngestionRun> = {}): IngestionRun {
  return {
    id: crypto.randomUUID(),
    provider: "football_data_couk",
    league: "EPL",
    season: "2023-2024",
    status: "success",
    fixtures_ingested: 380,
    odds_ingested: 760,
    error: null,
    triggered_by: "cron",
    started_at: "2026-07-16T00:00:00Z",
    finished_at: "2026-07-16T00:01:00Z",
    duration_ms: 60000,
    ...overrides,
  };
}

function runs(list: IngestionRun[]): IngestionRuns {
  return { runs: list, total: list.length, page: 1, per_page: 20 };
}

describe("nextPollInterval", () => {
  it("polls while a job runs and stops otherwise", () => {
    expect(nextPollInterval(runs([run({ status: "running" })]), 5000)).toBe(5000);
    expect(nextPollInterval(runs([run({ status: "success" })]), 5000)).toBe(false);
    expect(nextPollInterval(undefined, 5000)).toBe(false);
  });
});

describe("IngestionView", () => {
  beforeEach(() => vi.mocked(fetchIngestionRuns).mockResolvedValue(runs([run()])));
  afterEach(() => vi.clearAllMocks());

  it("renders runs with a status label", async () => {
    const { findByText } = renderWithProviders(<IngestionView />, { locale: "en" });
    expect(await findByText("Success")).toBeInTheDocument();
    expect(await findByText("380")).toBeInTheDocument();
  });

  it("requires a league and a season before re-scanning, then parses seasons", async () => {
    const { getByRole, getByLabelText, findByText } = renderWithProviders(<IngestionView />, {
      locale: "en",
    });
    await findByText("Success");

    const run_ = getByRole("button", { name: "Run re-scan" });
    expect(run_).toBeDisabled();

    fireEvent.click(getByLabelText("EPL"));
    fireEvent.change(getByLabelText(/Seasons/), {
      target: { value: "2023-2024, 2024-2025" },
    });
    expect(run_).toBeEnabled();

    fireEvent.click(run_);
    await waitFor(() => expect(rescan).toHaveBeenCalledWith(["EPL"], ["2023-2024", "2024-2025"]));
  });
});
