import { fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/test-utils";
import type { AdminUser, AdminUserList, Tier } from "@/types/admin";

import { UsersView } from "./UsersView";

vi.mock("@/lib/admin", () => ({
  fetchUsers: vi.fn(),
  fetchTiers: vi.fn(),
  fetchRedemptions: vi.fn(() => Promise.resolve([])),
  assignTier: vi.fn(() => Promise.resolve({})),
  setUserActive: vi.fn(() => Promise.resolve({})),
}));

import { assignTier, fetchTiers, fetchUsers, setUserActive } from "@/lib/admin";

function user(over: Partial<AdminUser> = {}): AdminUser {
  return {
    id: "u1",
    email: "alice@example.com",
    role: "user",
    base_tier: "free",
    effective_tier: "free",
    tier_expires_at: null,
    is_active: true,
    is_verified: true,
    created_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

function list(users: AdminUser[]): AdminUserList {
  return { users, total: users.length, page: 1, per_page: 25 };
}

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

describe("UsersView", () => {
  beforeEach(() => {
    vi.mocked(fetchUsers).mockResolvedValue(list([user()]));
    vi.mocked(fetchTiers).mockResolvedValue(TIERS);
  });
  afterEach(() => vi.clearAllMocks());

  it("renders the user list", async () => {
    const { findByText } = renderWithProviders(<UsersView />, { locale: "en" });
    expect(await findByText("alice@example.com")).toBeInTheDocument();
  });

  it("filters by tier", async () => {
    const { findByLabelText } = renderWithProviders(<UsersView />, { locale: "en" });
    fireEvent.change(await findByLabelText("Tier"), { target: { value: "pro" } });
    await waitFor(() =>
      expect(vi.mocked(fetchUsers).mock.calls.some((c) => c[0].tier === "pro")).toBe(true),
    );
  });

  it("disables a user", async () => {
    const { findByRole } = renderWithProviders(<UsersView />, { locale: "en" });
    fireEvent.click(await findByRole("button", { name: "Disable" }));
    await waitFor(() => expect(setUserActive).toHaveBeenCalledWith("u1", false));
  });

  it("grants a tier once one is picked", async () => {
    const { findByLabelText, findByRole } = renderWithProviders(<UsersView />, { locale: "en" });
    fireEvent.change(await findByLabelText("alice@example.com tier"), {
      target: { value: "t-pro" },
    });
    fireEvent.click(await findByRole("button", { name: "Grant" }));
    await waitFor(() => expect(assignTier).toHaveBeenCalled());
    expect(vi.mocked(assignTier).mock.calls.at(-1)?.[0]).toBe("u1");
    expect(vi.mocked(assignTier).mock.calls.at(-1)?.[1].tier_id).toBe("t-pro");
  });

  it("serializes expires_at as a tz-aware ISO timestamp, not a naive date", async () => {
    const { findByLabelText, findByRole } = renderWithProviders(<UsersView />, { locale: "en" });
    fireEvent.change(await findByLabelText("alice@example.com tier"), {
      target: { value: "t-pro" },
    });
    // The date input yields "2026-07-17"; the payload must be a full ISO
    // timestamp with a timezone (ends with Z or ±HH:MM), never the bare date.
    fireEvent.change(await findByLabelText("alice@example.com expires"), {
      target: { value: "2026-07-17" },
    });
    fireEvent.click(await findByRole("button", { name: "Grant" }));
    await waitFor(() => expect(assignTier).toHaveBeenCalled());
    const expires = vi.mocked(assignTier).mock.calls.at(-1)?.[1].expires_at;
    expect(expires).toMatch(/(Z|[+-]\d{2}:\d{2})$/);
    expect(expires).not.toBe("2026-07-17");
    expect(new Date(expires as string).toISOString()).toBe(expires);
  });

  it("sends null expires_at when no date is picked", async () => {
    const { findByLabelText, findByRole } = renderWithProviders(<UsersView />, { locale: "en" });
    fireEvent.change(await findByLabelText("alice@example.com tier"), {
      target: { value: "t-pro" },
    });
    fireEvent.click(await findByRole("button", { name: "Grant" }));
    await waitFor(() => expect(assignTier).toHaveBeenCalled());
    expect(vi.mocked(assignTier).mock.calls.at(-1)?.[1].expires_at).toBeNull();
  });
});
