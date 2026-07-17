import { fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/test-utils";
import type { LlmConfig, SpendReport } from "@/types/admin";

import { SpendView } from "./SpendView";

vi.mock("@/lib/admin", () => ({
  fetchSpend: vi.fn(),
  fetchLlmConfig: vi.fn(),
  updateLlmConfig: vi.fn(() => Promise.resolve()),
}));

// Recharts needs a sized container; stub ResponsiveContainer to a fixed box.
vi.mock("recharts", async () => {
  const actual = await vi.importActual<typeof import("recharts")>("recharts");
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
      <div style={{ width: 400, height: 200 }}>{children}</div>
    ),
  };
});

import { fetchLlmConfig, fetchSpend, updateLlmConfig } from "@/lib/admin";

function report(over: Partial<SpendReport> = {}): SpendReport {
  return {
    days: 30,
    since: "2026-01-01T00:00:00Z",
    daily: [{ day: "2026-07-10", tokens_in: 100, tokens_out: 50, cost: "0.30", count: 2 }],
    top_fixtures: [
      {
        fixture_id: "f1",
        home: "Arsenal",
        away: "Chelsea",
        league: "EPL",
        cost: "0.90",
        tokens_in: 200,
        tokens_out: 100,
        count: 3,
      },
    ],
    daily_token_budget: 100000,
    total_cost: "1.20",
    total_tokens: 450,
    ...over,
  };
}

function config(): LlmConfig {
  return {
    base_url: "",
    model: "gpt-x",
    key_masked: "••••abcd",
    max_tokens: 600,
    daily_token_budget: 100000,
    cache_ttl_seconds: 86400,
    cost_per_1k_in: "0.5",
    cost_per_1k_out: "1.5",
    is_enabled: true,
  };
}

describe("SpendView", () => {
  beforeEach(() => {
    vi.mocked(fetchSpend).mockResolvedValue(report());
    vi.mocked(fetchLlmConfig).mockResolvedValue(config());
  });
  afterEach(() => vi.clearAllMocks());

  it("renders totals and the top-fixtures table", async () => {
    const { findByText } = renderWithProviders(<SpendView />, { locale: "en" });
    expect(await findByText("$1.20")).toBeInTheDocument();
    expect(await findByText("Arsenal – Chelsea")).toBeInTheDocument();
  });

  it("refetches when the window changes", async () => {
    const { findByRole } = renderWithProviders(<SpendView />, { locale: "en" });
    fireEvent.click(await findByRole("button", { name: "7d" }));
    await waitFor(() => expect(vi.mocked(fetchSpend).mock.calls.some((c) => c[0] === 7)).toBe(true));
  });

  it("omits an empty api_key when saving config", async () => {
    const { findByRole, findByDisplayValue } = renderWithProviders(<SpendView />, { locale: "en" });
    // Wait for the config query to seed the form before saving.
    await findByDisplayValue("gpt-x");
    fireEvent.click(await findByRole("button", { name: "Save configuration" }));
    await waitFor(() => expect(updateLlmConfig).toHaveBeenCalled());
    const changes = vi.mocked(updateLlmConfig).mock.calls.at(-1)?.[0] ?? {};
    expect("api_key" in changes).toBe(false);
    expect(changes.model).toBe("gpt-x");
  });
});
