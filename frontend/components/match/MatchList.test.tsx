/**
 * The match list under F8's rules (F7): an error replaces the list only on a
 * first load; a failed background refetch (429, 5xx, network) keeps the cards
 * and says the data may be stale; after a 429 the next poll waits Retry-After.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import en from "@/messages/en.json";
import { summaryFixture } from "@/test/fixtures";

import { MatchList } from "./MatchList";

type Step = { status: number; body?: unknown; retryAfter?: number } | "network";

const LIST = { items: [summaryFixture], total: 1, limit: 30, offset: 0, matches_remaining: null };
const OK: Step = { status: 200, body: LIST };
const LIMITED = (retryAfter?: number): Step => ({
  status: 429,
  body: { detail: "Too many match list requests" },
  retryAfter,
});

function stubList(steps: Step[]): { calls: number; at: number[] } {
  const counter = { calls: 0, at: [] as number[] };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      const step = steps[Math.min(counter.calls, steps.length - 1)];
      counter.calls += 1;
      counter.at.push(Date.now());
      if (step === "network") throw new TypeError("Failed to fetch");
      const headers: Record<string, string> = {};
      if (step.retryAfter !== undefined) headers["retry-after"] = String(step.retryAfter);
      return Response.json(step.body ?? {}, { status: step.status, headers });
    }),
  );
  return counter;
}

function renderList(): void {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 30_000, refetchOnWindowFocus: false } },
  });
  render(
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <QueryClientProvider client={client}>
        <MatchList />
      </QueryClientProvider>
    </NextIntlClientProvider>,
  );
}

async function tick(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

const home = summaryFixture.home_team;

beforeEach(() => {
  vi.useFakeTimers({ now: new Date("2026-10-09T18:00:00Z") });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("MatchList: first load", () => {
  it("a 429 says so instead of the generic error", async () => {
    stubList([LIMITED(30)]);
    renderList();
    await tick(1);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Too many requests right now — the list will reload in a minute.",
    );
  });

  it("any other failure keeps the generic error", async () => {
    stubList([{ status: 500, body: { detail: "boom" } }]);
    renderList();
    await tick(1);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Could not load matches. Please try again later.",
    );
  });
});

describe("MatchList: a failed background refetch keeps the cards", () => {
  it.each<[string, Step]>([
    ["429", LIMITED(30)],
    ["500", { status: 500, body: { detail: "boom" } }],
    ["a network error", "network"],
  ])("%s: cards stay, the stale note is announced", async (_label, failure) => {
    stubList([OK, failure]);
    renderList();
    await tick(1);
    expect(screen.getByText(home)).toBeInTheDocument();

    await tick(60_000);

    expect(screen.getByText(home)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Couldn't refresh — showing data as of 18:00.",
    );
  });

  it("the note goes away once a refetch succeeds", async () => {
    stubList([OK, { status: 500, body: { detail: "boom" } }, OK]);
    renderList();
    await tick(1);
    await tick(60_000);
    expect(screen.getByRole("status")).toBeInTheDocument();
    await tick(60_000);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});

describe("MatchList: polling after a 429", () => {
  it("waits Retry-After when it is longer than the 60 s poll", async () => {
    const counter = stubList([OK, LIMITED(180), OK]);
    renderList();
    await tick(1);
    await tick(60_000); // the 429
    expect(counter.calls).toBe(2);
    await tick(170_000);
    expect(counter.calls).toBe(2);
    await tick(10_000);
    expect(counter.calls).toBe(3);
  });
});
