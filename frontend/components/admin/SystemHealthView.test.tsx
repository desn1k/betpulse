import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/test-utils";
import type { SystemHealth } from "@/types/admin";

import { SystemHealthView } from "./SystemHealthView";

vi.mock("@/lib/admin", () => ({
  fetchSystemHealth: vi.fn(),
  sendTestAlert: vi.fn(() => Promise.resolve({ status: "sent", detail: null })),
}));

import { fetchSystemHealth, sendTestAlert } from "@/lib/admin";

const health: SystemHealth = {
  status: "degraded",
  checked_at: "2026-07-19T00:00:00Z",
  components: [
    { name: "api", status: "ok", detail: "process reachable", latency_ms: null, meta: {} },
    { name: "postgres", status: "ok", detail: "reachable", latency_ms: 2, meta: {} },
    { name: "redis", status: "ok", detail: "reachable", latency_ms: 3, meta: {} },
    { name: "arq", status: "degraded", detail: "worker heartbeat not observed", latency_ms: 1, meta: { queue_depth: 2 } },
    { name: "llm_spend_today", status: "ok", detail: "UTC day token spend", latency_ms: null, meta: { tokens_total: 20 } },
    { name: "backup", status: "not_configured", detail: "backup checks land in Phase 14", latency_ms: null, meta: {} },
    { name: "ops_alerts", status: "not_configured", detail: "missing", latency_ms: null, meta: {} },
  ],
};

describe("SystemHealthView", () => {
  beforeEach(() => vi.mocked(fetchSystemHealth).mockResolvedValue(health));
  afterEach(() => vi.clearAllMocks());

  it("renders component statuses", async () => {
    const { findByText } = renderWithProviders(<SystemHealthView />, { locale: "en" });
    expect(await findByText("postgres")).toBeInTheDocument();
    expect(await findByText("redis")).toBeInTheDocument();
    expect(await findByText("ops_alerts")).toBeInTheDocument();
    expect(await findByText("llm_spend_today")).toBeInTheDocument();
    expect(await findByText("queue_depth")).toBeInTheDocument();
    expect(await findByText("20")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText("Not configured").length).toBeGreaterThan(0));
  });

  it("sends a test alert", async () => {
    const { findByRole, findByLabelText, findByText } = renderWithProviders(<SystemHealthView />, {
      locale: "en",
    });
    fireEvent.change(await findByLabelText("Message"), { target: { value: "hello ops" } });
    fireEvent.click(await findByRole("button", { name: "Send test alert" }));
    await waitFor(() => expect(sendTestAlert).toHaveBeenCalledWith("hello ops", expect.anything()));
    expect(await findByText("Test alert sent.")).toBeInTheDocument();
  });
});
