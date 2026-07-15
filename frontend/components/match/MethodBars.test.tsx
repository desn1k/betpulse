import { afterEach, describe, expect, it, vi } from "vitest";

import { detailFixture } from "@/test/fixtures";
import { renderWithProviders } from "@/test/test-utils";

import { MethodBars } from "./MethodBars";
import { ConsensusBar } from "./ConsensusBar";
import { MatchDetailView } from "./MatchDetailView";
import type { MatchDetail } from "@/types/match";

describe("MethodBars tier gating", () => {
  it("renders real bars for an unlocked (pro) tier", () => {
    const { container, queryByText } = renderWithProviders(
      <MethodBars methods={detailFixture.methods} locked={false} tierRequired="pro" />,
      { locale: "en" },
    );
    expect(container.querySelectorAll("[data-testid^='method-bar-']").length).toBe(3);
    expect(queryByText(/Available on the pro tier/)).not.toBeInTheDocument();
  });

  it("shows the lock and hides real bars when locked", () => {
    const { getByText, container } = renderWithProviders(
      // Locked with no data (guest/free): placeholder bars behind the lock.
      <MethodBars methods={[]} locked tierRequired="pro" />,
      { locale: "en" },
    );
    expect(getByText(/Available on the pro tier/)).toBeInTheDocument();
    expect(container.querySelectorAll("[data-testid^='method-bar-']").length).toBe(0);
  });
});

describe("ConsensusBar blur", () => {
  it("blurs the consensus and shows a sign-in hint for the guest tier", () => {
    const guest: MatchDetail = {
      ...detailFixture,
      flags: { methods: "blurred_consensus", per_half_totals: false, live_recompute: false },
    };
    const { getByText } = renderWithProviders(<ConsensusBar match={guest} />, { locale: "en" });
    expect(getByText(/Sign in to view the full breakdown/)).toBeInTheDocument();
  });

  it("does not blur for a paid tier", () => {
    const { queryByText } = renderWithProviders(<ConsensusBar match={detailFixture} />, {
      locale: "en",
    });
    expect(queryByText(/Sign in to view/)).not.toBeInTheDocument();
  });
});

describe("MatchDetailView limit handling", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the daily-limit upgrade message on a 403", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: { tier_required: "free" } }), {
            status: 403,
            headers: { "content-type": "application/json" },
          }),
      ),
    );
    const { findByText } = renderWithProviders(<MatchDetailView id="abc" />, { locale: "en" });
    expect(await findByText(/Daily match limit reached/)).toBeInTheDocument();
    expect(
      await findByText(/Upgrade to the free tier to view more matches today/),
    ).toBeInTheDocument();
  });
});
