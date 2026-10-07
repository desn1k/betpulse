import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { matchKeys } from "@/lib/queries";
import en from "@/messages/en.json";
import type { MatchDetail } from "@/types/match";

import { MatchDetailView } from "./MatchDetailView";

const ID = "m1";

const match: MatchDetail = {
  id: ID,
  league: { code: "EPL", name: "Premier League" },
  home_team: "Arsenal",
  away_team: "Chelsea",
  kickoff_at: "2026-10-08T18:00:00Z",
  kickoff_time_known: true,
  status: "scheduled",
  minute: null,
  home_score: null,
  away_score: null,
  consensus: { home: 0.5, draw: 0.3, away: 0.2 },
  champion_method: null,
  champion_accuracy_pct: null,
  last_polled_at: null,
  data_delayed: false,
  methods: [],
  market: null,
  model_agreement_pct: null,
  delta_vs_market: null,
  tier_required: "pro",
  flags: { methods: "blurred_consensus", per_half_totals: false, live_recompute: false },
};

type Step = { status: number; body?: unknown } | "network";

const OK: Step = { status: 200, body: match };
const QUOTA: Step = { status: 403, body: { detail: { tier_required: "free" } } };

/** Answer the match detail from ``steps`` (the last one repeats); count calls. */
function stubMatch(steps: Step[]): { calls: number } {
  const counter = { calls: 0 };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/api/matches/${ID}`)) {
        const step = steps[Math.min(counter.calls, steps.length - 1)];
        counter.calls += 1;
        if (step === "network") throw new TypeError("Failed to fetch");
        return Response.json(step.body ?? {}, { status: step.status });
      }
      // The analysis block: switched off, no request of its own matters here.
      return Response.json({ status: "disabled", not_a_probability_source: true });
    }),
  );
  return counter;
}

/** The app's query defaults (app/providers.tsx) minus retries, which
 * providers.test.tsx covers: focus refetch is off unless a query opts in. */
function renderView(): QueryClient {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 30_000, refetchOnWindowFocus: false } },
  });
  render(
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <QueryClientProvider client={client}>
        <MatchDetailView id={ID} />
      </QueryClientProvider>
    </NextIntlClientProvider>,
  );
  return client;
}

async function tick(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers({ now: new Date("2026-10-07T23:50:00Z") });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  focusManager.setFocused(undefined);
});

describe("MatchDetailView: first load", () => {
  it("shows the lock when the daily limit is spent", async () => {
    stubMatch([QUOTA]);
    renderView();
    await tick(1);
    expect(screen.getByText("Daily match limit reached")).toBeInTheDocument();
    expect(screen.queryByText("Arsenal")).not.toBeInTheDocument();
  });

  it("shows the error for an unknown match", async () => {
    stubMatch([{ status: 404, body: { detail: "Match not found" } }]);
    renderView();
    await tick(1);
    expect(screen.getByText("Could not load the match.")).toBeInTheDocument();
  });
});

describe("MatchDetailView: a failed background refetch keeps the card", () => {
  it.each<[string, Step]>([
    ["500", { status: 500, body: { detail: "boom" } }],
    ["502 (backend down)", { status: 502, body: { error: "backend_unavailable" } }],
    ["429", { status: 429, body: { detail: "Too many match requests" } }],
    ["404", { status: 404, body: { detail: "Match not found" } }],
    ["a network error", "network"],
  ])("%s: card stays, the stale note is announced", async (_label, failure) => {
    stubMatch([OK, failure]);
    renderView();
    await tick(1);
    expect(screen.getByText("Arsenal")).toBeInTheDocument();

    await tick(60_000);

    expect(screen.getByText("Arsenal")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Couldn't refresh — showing data as of 23:50.",
    );
  });

  it("403: card stays under the daily-limit banner", async () => {
    stubMatch([OK, QUOTA]);
    renderView();
    await tick(1);
    await tick(60_000);

    expect(screen.getByText("Arsenal")).toBeInTheDocument();
    const banner = screen.getByRole("status");
    expect(banner).toHaveTextContent("Daily match limit reached");
    expect(banner).toHaveTextContent("Upgrade to the free tier to view more matches today.");
    expect(banner).toHaveTextContent("Below is the last data received.");
  });
});

describe("MatchDetailView: polling after a failure", () => {
  it("stops polling a match that is gone (404)", async () => {
    const counter = stubMatch([OK, { status: 404, body: { detail: "Match not found" } }]);
    renderView();
    await tick(1);
    await tick(60_000);
    expect(counter.calls).toBe(2);

    await tick(10 * 60_000);

    expect(counter.calls).toBe(2);
  });

  it("after a 403 the next fetch is at the next UTC midnight (+ up to 60 s)", async () => {
    const counter = stubMatch([OK, QUOTA, OK]);
    renderView();
    await tick(1); // 23:50:00
    await tick(60_000); // 23:51:00 → 403
    expect(counter.calls).toBe(2);

    await tick(8 * 60_000 + 59_000); // 23:59:59
    expect(counter.calls).toBe(2);

    await tick(61_001); // 00:01:00.001: past midnight + the largest jitter
    expect(counter.calls).toBe(3);
    expect(screen.getByText("Arsenal")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("after a 403 the page refetches when the window regains focus", async () => {
    const counter = stubMatch([OK, QUOTA, OK]);
    renderView();
    await tick(1);
    await tick(60_000);
    expect(screen.getByRole("status")).toHaveTextContent("Daily match limit reached");

    await act(async () => {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    });
    await tick(1);

    expect(counter.calls).toBe(3);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("after a 403 a promo redemption (match queries invalidated) recovers the page", async () => {
    const counter = stubMatch([OK, QUOTA, OK]);
    const client = renderView();
    await tick(1);
    await tick(60_000);

    await act(async () => {
      await client.invalidateQueries({ queryKey: matchKeys.all });
    });
    await tick(1);

    expect(counter.calls).toBe(3);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
