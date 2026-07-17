import { fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/test-utils";
import type { Tier } from "@/types/admin";

import { TiersView } from "./TiersView";

vi.mock("@/lib/admin", () => ({
  fetchTiers: vi.fn(),
  updateTier: vi.fn(() => Promise.resolve({})),
}));

import { fetchTiers, updateTier } from "@/lib/admin";

const PRO: Tier = {
  id: "t-pro",
  name: "pro",
  price: "9.99",
  period: "month",
  feature_flags: { llm: "top5" },
  limits: { matches_per_day: -1 },
  is_public: true,
  sort_order: 2,
};

describe("TiersView", () => {
  beforeEach(() => {
    vi.mocked(fetchTiers).mockResolvedValue([PRO]);
  });
  afterEach(() => vi.clearAllMocks());

  it("renders a tier card with its price", async () => {
    const { findByLabelText } = renderWithProviders(<TiersView />, { locale: "en" });
    expect(await findByLabelText("pro price")).toHaveValue(9.99);
  });

  it("saves parsed JSON", async () => {
    const { findByRole } = renderWithProviders(<TiersView />, { locale: "en" });
    fireEvent.click(await findByRole("button", { name: "Save" }));
    await waitFor(() => expect(updateTier).toHaveBeenCalled());
    const changes = vi.mocked(updateTier).mock.calls.at(-1)?.[1];
    expect(changes?.feature_flags).toEqual({ llm: "top5" });
  });

  it("shows an error on invalid JSON and does not save", async () => {
    const { findByLabelText, findByRole } = renderWithProviders(<TiersView />, {
      locale: "en",
    });
    fireEvent.change(await findByLabelText("pro limits"), { target: { value: "{ not json" } });
    fireEvent.click(await findByRole("button", { name: "Save" }));
    expect(await findByRole("alert")).toBeInTheDocument();
    expect(updateTier).not.toHaveBeenCalled();
  });
});
