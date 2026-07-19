import { fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/test-utils";
import type { AuditLogList } from "@/types/admin";

import { AuditView } from "./AuditView";

vi.mock("@/lib/admin", () => ({
  fetchAudit: vi.fn(),
}));

import { fetchAudit } from "@/lib/admin";

const audit: AuditLogList = {
  total: 1,
  page: 1,
  per_page: 25,
  events: [
    {
      id: "a1",
      actor_user_id: "u1",
      actor_email: "admin@example.com",
      action: "user.disable",
      target: "user:u1",
      ip: "127.0.0.1",
      user_agent: null,
      meta: { revoked_tokens: 2 },
      created_at: "2026-07-19T00:00:00Z",
    },
  ],
};

describe("AuditView", () => {
  beforeEach(() => vi.mocked(fetchAudit).mockResolvedValue(audit));
  afterEach(() => vi.clearAllMocks());

  it("renders audit events", async () => {
    const { findByText } = renderWithProviders(<AuditView />, { locale: "en" });
    expect(await findByText("user.disable")).toBeInTheDocument();
    expect(await findByText("admin@example.com")).toBeInTheDocument();
  });

  it("passes filters to the fetcher", async () => {
    const { findByLabelText } = renderWithProviders(<AuditView />, { locale: "en" });
    fireEvent.change(await findByLabelText("Search"), { target: { value: "disable" } });
    await waitFor(() => expect(fetchAudit).toHaveBeenLastCalledWith({ q: "disable", action: "", page: 1 }));
  });
});
