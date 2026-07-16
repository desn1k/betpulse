import { fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/test-utils";
import type { Model, ModelsResponse, WeightingMode } from "@/types/admin";

import { ModelsView } from "./ModelsView";

vi.mock("@/lib/admin", () => ({
  fetchModels: vi.fn(),
  fetchSnapshots: vi.fn(() => Promise.resolve([])),
  fetchSnapshotDiff: vi.fn(),
  patchModel: vi.fn(() => Promise.resolve()),
  promoteModel: vi.fn(),
  demoteModel: vi.fn(() => Promise.resolve()),
  retrainModels: vi.fn(() => Promise.resolve()),
  rollbackSnapshot: vi.fn(() => Promise.resolve()),
  setWeightingMode: vi.fn(),
  setWeights: vi.fn(() => Promise.resolve()),
}));

import {
  fetchModels,
  promoteModel,
  retrainModels,
  setWeightingMode,
  setWeights,
} from "@/lib/admin";

function model(over: Partial<Model> = {}): Model {
  return {
    id: crypto.randomUUID(),
    method: "elo",
    version: "v1",
    status: "challenger",
    accuracy_pct: 60,
    brier: 0.2,
    log_loss: 0.5,
    roi_vs_closing: 1.2,
    sample_count: 500,
    is_enabled: true,
    is_visible: true,
    display_weight: 50,
    min_samples: 300,
    notes: null,
    last_trained_at: null,
    last_evaluated_at: null,
    ...over,
  };
}

function response(mode: WeightingMode, models: Model[]): ModelsResponse {
  return { models, weighting_mode: mode };
}

describe("ModelsView", () => {
  beforeEach(() => {
    vi.mocked(setWeightingMode).mockResolvedValue(response("auto", []));
    vi.mocked(promoteModel).mockResolvedValue({ promoted: true, warning: null });
  });
  afterEach(() => vi.clearAllMocks());

  it("renders the model table", async () => {
    vi.mocked(fetchModels).mockResolvedValue(
      response("auto", [model({ method: "elo", status: "champion" })]),
    );
    const { findByText } = renderWithProviders(<ModelsView />, { locale: "en" });
    expect(await findByText("elo")).toBeInTheDocument();
    expect(await findByText("Champion")).toBeInTheDocument();
  });

  it("switches the weighting mode", async () => {
    vi.mocked(fetchModels).mockResolvedValue(response("auto", [model()]));
    const { findByRole } = renderWithProviders(<ModelsView />, { locale: "en" });
    fireEvent.click(await findByRole("button", { name: "Manual" }));
    await waitFor(() => expect(setWeightingMode).toHaveBeenCalled());
    expect(vi.mocked(setWeightingMode).mock.calls.some((c) => c[0] === "manual")).toBe(true);
  });

  it("saves manual weights only when they sum to 100", async () => {
    vi.mocked(fetchModels).mockResolvedValue(
      response("manual", [
        model({ method: "elo", display_weight: 70 }),
        model({ method: "xg", display_weight: 30 }),
      ]),
    );
    const { findByRole } = renderWithProviders(<ModelsView />, { locale: "en" });
    const save = await findByRole("button", { name: "Save weights" });
    expect(save).toBeEnabled(); // 70 + 30 = 100

    fireEvent.change(await findByRole("spinbutton", { name: "elo weight" }), {
      target: { value: "10" },
    });
    expect(save).toBeDisabled(); // 10 + 30 != 100

    fireEvent.change(await findByRole("spinbutton", { name: "elo weight" }), {
      target: { value: "70" },
    });
    fireEvent.click(save);
    await waitFor(() => expect(setWeights).toHaveBeenCalled());
    expect(vi.mocked(setWeights).mock.calls.at(-1)?.[0]).toEqual({ elo: 70, xg: 30 });
  });

  it("shows a warning after promoting below the minimum samples", async () => {
    vi.mocked(fetchModels).mockResolvedValue(
      response("auto", [model({ method: "xg", sample_count: 50 })]),
    );
    vi.mocked(promoteModel).mockResolvedValue({ promoted: true, warning: "below_min_samples" });
    const { findByRole, findByText } = renderWithProviders(<ModelsView />, { locale: "en" });
    fireEvent.click(await findByRole("button", { name: "Promote" }));
    await waitFor(() => expect(promoteModel).toHaveBeenCalled());
    expect(await findByText(/below the minimum sample count/)).toBeInTheDocument();
  });

  it("triggers a retrain", async () => {
    vi.mocked(fetchModels).mockResolvedValue(response("auto", [model()]));
    const { findByRole } = renderWithProviders(<ModelsView />, { locale: "en" });
    fireEvent.click(await findByRole("button", { name: "Retrain all" }));
    await waitFor(() => expect(retrainModels).toHaveBeenCalled());
  });
});
