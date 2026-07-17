import { fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/test-utils";
import type { PromoBatch, Tier } from "@/types/admin";

import { PromoView } from "./PromoView";

vi.mock("@/lib/admin", () => ({
  fetchBatches: vi.fn(),
  fetchTiers: vi.fn(),
  createBatch: vi.fn(),
  killBatch: vi.fn(() => Promise.resolve({})),
}));

import { createBatch, fetchBatches, fetchTiers, killBatch } from "@/lib/admin";

const TIERS: Tier[] = [
  {
    id: "t-pro",
    name: "pro",
    price: "9.99",
    period: "month",
    feature_flags: {},
    limits: {},
    is_public: true,
    sort_order: 2,
  },
];

function batch(over: Partial<PromoBatch> = {}): PromoBatch {
  return {
    id: "b1",
    name: "Launch",
    code_type: "upgrade",
    value: null,
    tier_id: "t-pro",
    bound_user_id: null,
    max_activations: 1,
    size: 500,
    stackable: false,
    expires_at: null,
    status: "active",
    created_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

describe("PromoView", () => {
  beforeEach(() => {
    vi.mocked(fetchBatches).mockResolvedValue([batch()]);
    vi.mocked(fetchTiers).mockResolvedValue(TIERS);
    vi.mocked(createBatch).mockResolvedValue({
      batch: batch(),
      codes: ["AAAA-BBBB-CCCC"],
      warning: "plaintext_codes_shown_once",
    });
  });
  afterEach(() => vi.clearAllMocks());

  it("hides the value field for upgrade codes and shows it otherwise", async () => {
    const { findByLabelText, queryByLabelText } = renderWithProviders(<PromoView />, {
      locale: "en",
    });
    // Default code type is "upgrade" → no value field.
    await findByLabelText("Code type");
    expect(queryByLabelText("Value")).toBeNull();

    fireEvent.change(await findByLabelText("Code type"), { target: { value: "percent" } });
    expect(await findByLabelText("Value")).toBeInTheDocument();
  });

  it("only shows the bound-user field when bind is checked", async () => {
    const { findByLabelText, queryByLabelText } = renderWithProviders(<PromoView />, {
      locale: "en",
    });
    await findByLabelText("Code type");
    expect(queryByLabelText("Bound user ID")).toBeNull();

    fireEvent.click(await findByLabelText("Bind to user"));
    expect(await findByLabelText("Bound user ID")).toBeInTheDocument();
  });

  it("blocks generation when size is not a multiple of 500", async () => {
    const { findByLabelText, findByRole } = renderWithProviders(<PromoView />, { locale: "en" });
    fireEvent.change(await findByLabelText("Name"), { target: { value: "X" } });
    fireEvent.change(await findByLabelText("Size"), { target: { value: "600" } });
    const generate = await findByRole("button", { name: "Generate" });
    expect(generate).toBeDisabled();

    fireEvent.change(await findByLabelText("Size"), { target: { value: "1000" } });
    expect(generate).toBeEnabled();
  });

  it("kills a batch", async () => {
    const { findByRole } = renderWithProviders(<PromoView />, { locale: "en" });
    fireEvent.click(await findByRole("button", { name: "Kill batch" }));
    await waitFor(() => expect(killBatch).toHaveBeenCalled());
    expect(vi.mocked(killBatch).mock.calls.at(-1)?.[0]).toBe("b1");
  });
});
