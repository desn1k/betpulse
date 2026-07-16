import { fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/test-utils";
import type { Provider } from "@/types/admin";

import { ProvidersView } from "./ProvidersView";

vi.mock("@/lib/admin", () => ({
  fetchProviders: vi.fn(),
  createProvider: vi.fn(() => Promise.resolve()),
  deleteProvider: vi.fn(() => Promise.resolve()),
  setProviderEnabled: vi.fn(() => Promise.resolve()),
}));

import { createProvider, fetchProviders, setProviderEnabled } from "@/lib/admin";

const provider: Provider = {
  id: "p1",
  name: "api_football",
  roles: ["live", "odds"],
  priority: 10,
  key_masked: "••••xyz9",
  requests_per_minute: null,
  requests_per_day: 7500,
  quota_state: {},
  is_enabled: true,
};

describe("ProvidersView", () => {
  beforeEach(() => vi.mocked(fetchProviders).mockResolvedValue([provider]));
  afterEach(() => vi.clearAllMocks());

  it("lists providers with the masked key", async () => {
    const { findByText } = renderWithProviders(<ProvidersView />, { locale: "en" });
    expect(await findByText("api_football")).toBeInTheDocument();
    expect(await findByText("••••xyz9")).toBeInTheDocument();
  });

  it("keeps Save disabled until a name and a role are set, then creates", async () => {
    const { getByRole, getByLabelText, findByText } = renderWithProviders(<ProvidersView />, {
      locale: "en",
    });
    await findByText("api_football");

    const save = getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();

    fireEvent.change(getByLabelText("Name"), { target: { value: "sportmonks" } });
    fireEvent.click(getByLabelText("odds"));
    expect(save).toBeEnabled();

    fireEvent.click(save);
    await waitFor(() => expect(createProvider).toHaveBeenCalled());
    expect(vi.mocked(createProvider).mock.calls[0][0]).toMatchObject({
      name: "sportmonks",
      roles: ["odds"],
    });
  });

  it("toggles a provider's enabled state", async () => {
    const { findByRole } = renderWithProviders(<ProvidersView />, { locale: "en" });
    fireEvent.click(await findByRole("button", { name: "Disable" }));
    await waitFor(() => expect(setProviderEnabled).toHaveBeenCalledWith("p1", false));
  });
});
